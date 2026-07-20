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
import { DeployableBadge, ErrorState, Loading } from "./ui";
import { theme } from "../theme";
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
 * /strategy/validation-trend`'s three sections, each backed by every
 * `reports/*_validation_summary.json` on disk regardless of Pilot mapping:
 *
 * 1. A flat table of every validated strategy's current gate snapshot.
 * 2. A multi-strategy, metric-selectable run-over-run trend chart (PBO/DSR/
 *    Sharpe/Max Drawdown) — only strategies with >= 2 recorded harness runs
 *    are plotted (CONSTRAINT #4: never a fabricated single-point trend).
 * 3. A macro-regime TRANSITION timeline from the rotated `output/history/`
 *    snapshots (only rows where the regime differs from the immediately
 *    preceding rotated snapshot, not every raw snapshot).
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

const CHART_COLORS = ["#38bdf8", "#10b981", "#f59e0b", "#a855f7", "#ec4899", "#14b8a6"];

function fmtGateNum(key: MetricKey, value: number | null): string {
  if (value == null) return "—";
  if (key === "max_drawdown") return fmtPct(value, 0, { fromFraction: true });
  return fmtNum(value, 2);
}

/** RISK ON -> growth, RECESSION/CREDIT EVENT -> decline, everything else
 * (NEUTRAL, RISK OFF, UNKNOWN, ...) -> caution. Never guesses at a regime
 * string that wasn't actually persisted. Mirrors Observability.tsx's own
 * local `regimeColor` (kept as an independent copy here rather than an
 * export, to keep this component's diff isolated from that screen). */
function regimeColor(regime: string): string {
  const r = regime.toUpperCase();
  if (r.includes("RISK ON")) return theme.growth;
  if (r.includes("RECESSION") || r.includes("CREDIT EVENT")) return theme.decline;
  return theme.caution;
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
        style={{ marginBottom: 16 }}
        data-testid="validation-trend-strategies"
      >
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>All validated strategies</h2>
        <p style={{ margin: "0 0 12px", fontSize: 13, color: theme.textMuted }}>
          Every validated strategy on disk, including ones not yet wired to a Pilot above.
        </p>
        {data.strategies.length === 0 ? (
          <div className="empty" data-testid="validation-trend-strategies-empty">
            {data.strategies_reason ?? "No validated strategies yet."}
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
                  <th style={{ padding: 8 }}>Strategy</th>
                  <th style={{ padding: 8 }}>Status</th>
                  <th style={{ padding: 8 }}>PBO</th>
                  <th style={{ padding: 8 }}>DSR</th>
                  <th style={{ padding: 8 }}>Sharpe</th>
                  <th style={{ padding: 8 }}>Max DD</th>
                  <th style={{ padding: 8 }}>Stress gate</th>
                  <th style={{ padding: 8 }}>Report date</th>
                </tr>
              </thead>
              <tbody>
                {data.strategies.map((s) => (
                  <tr
                    key={s.strategy_id}
                    style={{ borderBottom: `1px solid ${theme.border}` }}
                    data-testid={`validation-trend-row-${s.strategy_id}`}
                  >
                    <td style={{ padding: 8, fontWeight: 600 }}>{s.strategy_id}</td>
                    <td style={{ padding: 8 }}>
                      <DeployableBadge deployable={s.deployable} />
                    </td>
                    <td style={{ padding: 8 }} className="num">{fmtGateNum("pbo", s.pbo)}</td>
                    <td style={{ padding: 8 }} className="num">{fmtGateNum("dsr", s.dsr)}</td>
                    <td style={{ padding: 8 }} className="num">{fmtGateNum("sharpe", s.sharpe)}</td>
                    <td style={{ padding: 8 }} className="num">
                      {fmtGateNum("max_drawdown", s.max_drawdown)}
                    </td>
                    <td style={{ padding: 8 }}>
                      {!s.is_options_selling
                        ? "n/a"
                        : s.stress_gate_passed == null
                          ? "—"
                          : s.stress_gate_passed
                            ? "✓ passed"
                            : "✗ failed"}
                    </td>
                    <td style={{ padding: 8, color: theme.textMuted }}>{s.report_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section
        className="card card-pad"
        style={{ marginBottom: 16 }}
        data-testid="validation-trend-chart"
      >
        <div
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, gap: 8 }}
        >
          <h2 style={{ fontSize: 16, margin: 0 }}>Validation trend across strategies</h2>
          {strategiesWithTrend.length > 0 && (
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value as MetricKey)}
              data-testid="validation-trend-metric-select"
              style={{
                background: theme.surface2,
                color: theme.textSecondary,
                border: `1px solid ${theme.border}`,
                borderRadius: 6,
                padding: "4px 8px",
                fontSize: 12,
              }}
            >
              {(Object.keys(METRIC_LABELS) as MetricKey[]).map((key) => (
                <option key={key} value={key}>
                  {METRIC_LABELS[key]}
                </option>
              ))}
            </select>
          )}
        </div>
        <p style={{ margin: "0 0 12px", fontSize: 13, color: theme.textMuted }}>
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
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                <XAxis
                  dataKey="date"
                  tickFormatter={fmtDate}
                  stroke={theme.textMuted}
                  fontSize={10}
                  tickLine={false}
                />
                <YAxis stroke={theme.textMuted} fontSize={10} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: theme.surface2, border: `1px solid ${theme.border}`, borderRadius: 4 }}
                  labelStyle={{ color: theme.textSecondary, fontSize: 11 }}
                  itemStyle={{ fontSize: 11 }}
                  labelFormatter={(d) => fmtDate(String(d))}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {strategiesWithTrend.map((sid, index) => (
                  <Line
                    key={sid}
                    type="monotone"
                    dataKey={sid}
                    stroke={CHART_COLORS[index % CHART_COLORS.length]}
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

      <section
        className="card card-pad"
        style={{ marginBottom: 16 }}
        data-testid="validation-trend-regime"
      >
        <h2 style={{ fontSize: 16, margin: "0 0 4px" }}>Macro regime timeline</h2>
        <p style={{ margin: "0 0 12px", fontSize: 13, color: theme.textMuted }}>
          {data.n_rotated_snapshots} rotated snapshot{data.n_rotated_snapshots === 1 ? "" : "s"} available
          in output/history/; only regime CHANGES are listed below.
        </p>
        {data.regime_timeline.length === 0 ? (
          <div className="empty" data-testid="validation-trend-regime-empty">
            {data.regime_reason ?? "No regime timeline yet."}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="validation-trend-regime-list">
            {data.regime_timeline.map((t, i) => (
              <div
                key={`${t.timestamp}-${i}`}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "6px 10px",
                  background: theme.surface2,
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                <span style={{ color: theme.textMuted, fontSize: 12 }}>{fmtDate(t.timestamp)}</span>
                <span style={{ color: regimeColor(t.market_regime), fontWeight: 700 }}>
                  {t.market_regime}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
