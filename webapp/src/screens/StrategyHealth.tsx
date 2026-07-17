import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { CurvePoint, StrategyHealthGate, StrategyHealthRow } from "../api/types";
import { useApi } from "../hooks/useApi";
import { DeployableBadge, ErrorState, Loading } from "../components/ui";
import { Sparkline } from "../components/charts";
import { fmtNum, fmtPct } from "../format";
import { theme } from "../theme";

/**
 * Strategy Health — a bird's-eye deployability-gate dashboard across EVERY
 * Pilot at once. `PilotDetail`'s `HonestyRow` already shows one Pilot's
 * pass/fail badge; this screen is the catalog-wide view that additionally
 * breaks down WHICH gate failed and what the actual value was versus the
 * required threshold (ported from the retired Streamlit Command Center's
 * "Strategy Health" section, see gui/panels/gravity_audit.py).
 *
 * Read-only, informational — cards are not clickable/linked (mirrors
 * Models.tsx: a registry-style listing, not a navigation surface).
 */

const GATE_SHORT_LABEL: Record<StrategyHealthGate["key"], string> = {
  pbo: "PBO",
  dsr: "DSR",
  sharpe: "Sharpe",
  max_drawdown: "Max DD",
};

function directionGlyph(direction: StrategyHealthGate["direction"]): string {
  return direction === "below" ? "<" : ">";
}

function formatGateNumber(gate: StrategyHealthGate, value: number): string {
  if (gate.key === "max_drawdown") return fmtPct(value, 0, { fromFraction: true });
  return fmtNum(value, gate.key === "pbo" ? 2 : 2);
}

function GateChip({ gate }: { gate: StrategyHealthGate }) {
  const cls =
    gate.passed == null ? "badge badge-neutral" : gate.passed ? "badge badge-good" : "badge badge-bad";
  const valueStr = gate.value == null ? "—" : formatGateNumber(gate, gate.value);
  return (
    <span className={cls} title={gate.label}>
      {GATE_SHORT_LABEL[gate.key]} {valueStr}{" "}
      <span style={{ opacity: 0.75 }}>
        ({directionGlyph(gate.direction)} {formatGateNumber(gate, gate.threshold)})
      </span>
    </span>
  );
}

function StressGateChip({ passed }: { passed: boolean | null }) {
  if (passed == null) return null;
  return (
    <span
      className={passed ? "badge badge-good" : "badge badge-bad"}
      title="Tail-scenario stress gate: survives OCT 2008 / FEB 2018 / MAR 2020 / AUG 2024 with < 50% drawdown"
    >
      Stress {passed ? "✓ passed" : "✗ failed"}
    </span>
  );
}

/** DSR run-over-run, the primary deployability metric, as a tiny sparkline. */
function trendToCurve(row: StrategyHealthRow): CurvePoint[] {
  return row.trend
    .filter((t): t is typeof t & { report_date: string; dsr: number } => t.report_date != null && t.dsr != null)
    .map((t) => ({ date: t.report_date, value: t.dsr }));
}

function HealthCard({ row }: { row: StrategyHealthRow }) {
  const hasGates = row.gates.length > 0;
  const curve = useMemo(() => trendToCurve(row), [row]);

  return (
    <section className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 15, wordBreak: "break-word" }}>
            {row.pilot_name}
          </div>
          <div style={{ color: theme.textMuted, fontSize: 12, marginTop: 2 }}>
            {row.strategy_id ? `backtest: ${row.strategy_id}` : "no backtest joined"}
          </div>
        </div>
        <DeployableBadge deployable={row.deployable} />
      </div>

      {hasGates ? (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {row.gates.map((g) => (
              <GateChip key={g.key} gate={g} />
            ))}
            {row.is_options_selling === true && <StressGateChip passed={row.stress_gate_passed} />}
          </div>
          {row.report_date && (
            <div style={{ color: theme.textMuted, fontSize: 11, marginTop: 8 }}>
              Report date {row.report_date}
            </div>
          )}
          {curve.length >= 2 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11, color: theme.textMuted, marginBottom: 2 }}>
                DSR, last {curve.length} runs
              </div>
              <Sparkline data={curve} positive={curve[curve.length - 1].value >= curve[0].value} />
            </div>
          )}
        </>
      ) : (
        <p style={{ color: theme.textSecondary, fontSize: 12.5, lineHeight: 1.5, marginTop: 12 }}>
          {row.reason ?? "No validation data available for this pilot."}
        </p>
      )}
    </section>
  );
}

export function StrategyHealth() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<StrategyHealthRow[]>(
    () => api.getStrategyHealth(),
    []
  );
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/marketplace"));

  const summary = useMemo(() => {
    if (!data) return null;
    const evaluated = data.filter((r) => r.gates.length > 0);
    const deployableCount = evaluated.filter((r) => r.deployable === true).length;
    const noBacktestCount = data.length - evaluated.length;
    return { total: data.length, evaluated: evaluated.length, deployableCount, noBacktestCount };
  }, [data]);

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Pilots
      </button>
      <h1 className="screen-title">Strategy health</h1>
      <p className="screen-sub">
        Every Pilot's underlying validated strategy, and the actual per-gate
        value behind its deployable badge — never just the pass/fail verdict.
      </p>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            No pilots in the catalog yet.
          </div>
        ) : (
          <>
            {summary && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "4px 0 14px" }}>
                <span className="chip">
                  {summary.deployableCount}/{summary.evaluated} evaluated deployable
                </span>
                {summary.noBacktestCount > 0 && (
                  <span className="chip">
                    {summary.noBacktestCount} without a backtest yet
                  </span>
                )}
              </div>
            )}
            {data.map((row) => (
              <HealthCard key={row.pilot_id} row={row} />
            ))}
          </>
        )
      )}

      <p
        style={{
          color: theme.textMuted,
          fontSize: 11.5,
          marginTop: 20,
          textAlign: "center",
          lineHeight: 1.5,
        }}
      >
        Deployable requires PBO &lt; 0.50, DSR &gt; 0.95, net Sharpe &gt; 0.50, Max
        Drawdown &lt; 30% — plus a tail-scenario stress gate for options-selling
        strategies. Thresholds are never loosened to force a green badge.
      </p>
    </div>
  );
}
