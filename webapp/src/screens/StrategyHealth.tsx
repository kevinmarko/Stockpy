import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  CurvePoint,
  GravityAiAuditStep,
  GravityAuditStatus,
  StrategyHealthGate,
  StrategyHealthRow,
  Thresholds,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { DeployableBadge, ErrorState, Loading } from "../components/ui";
import { Sparkline } from "../components/charts";
import { TabGuide } from "../components/TabGuide";
import { ValidationTrend } from "../components/ValidationTrend";
import { loadThresholds } from "../help/thresholds";
import { fmtNum, fmtPct, timeAgo } from "../format";
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

/**
 * `stressMaxDrawdown` is `validation.thresholds.STRESS_MAX_DRAWDOWN`, live-read
 * from `GET /thresholds` (never re-typed as a literal here) — `null` while the
 * fetch is in flight or failed renders "—" rather than a guessed limit.
 */
function StressGateChip({
  passed,
  stressMaxDrawdown,
}: {
  passed: boolean | null;
  stressMaxDrawdown: number | null;
}) {
  if (passed == null) return null;
  const ddText =
    stressMaxDrawdown == null ? "—" : fmtPct(stressMaxDrawdown, 0, { fromFraction: true });
  return (
    <span
      className={passed ? "badge badge-good" : "badge badge-bad"}
      title={`Tail-scenario stress gate: survives OCT 2008 / FEB 2018 / MAR 2020 / AUG 2024 with < ${ddText} drawdown`}
    >
      Stress {passed ? "✓ passed" : "✗ failed"}
    </span>
  );
}

/** Which run-over-run metric the sparkline currently plots. */
type TrendMetricKey = StrategyHealthGate["key"];

// Whether a LOWER or HIGHER value is the "better" direction for each metric —
// mirrors pilots/strategy_health.py's `_GATE_SPECS` tuple exactly (never
// re-guessed here): pbo/max_drawdown are "below" (lower is better), dsr/sharpe
// are "above" (higher is better). Drives the sparkline's green/red coloring so
// e.g. a FALLING PBO trend still renders as "positive", not red.
const TREND_METRIC_DIRECTION: Record<TrendMetricKey, "above" | "below"> = {
  pbo: "below",
  dsr: "above",
  sharpe: "above",
  max_drawdown: "below",
};

/** Run-over-run values for the selected metric, as a tiny sparkline. */
function trendToCurve(row: StrategyHealthRow, metric: TrendMetricKey): CurvePoint[] {
  return row.trend
    .filter((t): t is typeof t & { report_date: string } =>
      t.report_date != null && t[metric] != null
    )
    .map((t) => ({ date: t.report_date, value: t[metric] as number }));
}

function HealthCard({
  row,
  thresholds,
  metric,
}: {
  row: StrategyHealthRow;
  thresholds: Thresholds | null;
  metric: TrendMetricKey;
}) {
  const hasGates = row.gates.length > 0;
  const curve = useMemo(() => trendToCurve(row, metric), [row, metric]);
  const direction = TREND_METRIC_DIRECTION[metric];
  const trendingBetter =
    curve.length >= 2
      ? direction === "below"
        ? curve[curve.length - 1].value <= curve[0].value
        : curve[curve.length - 1].value >= curve[0].value
      : true;

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
            {row.is_options_selling === true && (
              <StressGateChip
                passed={row.stress_gate_passed}
                stressMaxDrawdown={thresholds?.stress_max_drawdown ?? null}
              />
            )}
          </div>
          {row.report_date && (
            <div style={{ color: theme.textMuted, fontSize: 11, marginTop: 8 }}>
              Report date {row.report_date}
            </div>
          )}
          {curve.length >= 2 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11, color: theme.textMuted, marginBottom: 2 }}>
                {GATE_SHORT_LABEL[metric]}, last {curve.length} runs
              </div>
              <Sparkline data={curve} positive={trendingBetter} />
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

// ---------------------------------------------------------------------------
// Gravity Audit — read-only port of the retired Streamlit Command Center's
// Safety tab (gui/panels/gravity_audit.py). Two independent sub-sections:
// the AI Gravity audit runner (Claude auditor + Gemini cross-checker) and the
// legacy, purely structural Gravity Review Suite. DELIBERATELY no "run a new
// audit" trigger on either — both are real-cost/multi-minute operations with
// no incremental-progress channel over this API's request/response shape.
// See GET /gravity/audit-status's own docstring (api/pilots_api.py) for the
// full reasoning.
// ---------------------------------------------------------------------------

const thL: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  color: theme.textMuted,
  fontWeight: 600,
  borderBottom: `1px solid ${theme.border}`,
};
const tdL: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  borderBottom: `1px solid ${theme.border}`,
  verticalAlign: "top",
};

const AI_HEALTH_STYLE: Record<
  GravityAuditStatus["ai_audit"]["health"],
  { color: string; background: string; border: string }
> = {
  clean: { color: theme.growth, background: "rgba(16, 185, 129, 0.1)", border: "rgba(16, 185, 129, 0.28)" },
  warn: { color: theme.caution, background: "rgba(245, 158, 11, 0.1)", border: "rgba(245, 158, 11, 0.28)" },
  fail: { color: theme.decline, background: "rgba(239, 68, 68, 0.1)", border: "rgba(239, 68, 68, 0.28)" },
  empty: { color: theme.textMuted, background: theme.surface2, border: theme.border },
};

const AI_STATUS_NOTE: Record<GravityAuditStatus["ai_audit"]["status"], string | null> = {
  disabled:
    "AI Gravity runner is off. Set GRAVITY_AI_RUNNER_ENABLED=true plus ANTHROPIC_API_KEY and GEMINI_API_KEY on the desktop console to enable it — the structural audit below is unaffected.",
  missing_key:
    "GRAVITY_AI_RUNNER_ENABLED is on but neither ANTHROPIC_API_KEY nor GEMINI_API_KEY is set.",
  partial_key:
    "Only one provider key is configured — the runner records the missing side as skipped; disagreement detection needs both.",
  ready: null,
};

function Banner({
  color,
  background,
  border,
  children,
}: {
  color: string;
  background: string;
  border: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        color,
        background,
        border: `1px solid ${border}`,
        borderRadius: "var(--r-md)",
        padding: "10px 12px",
        fontSize: 12.5,
        lineHeight: 1.45,
      }}
    >
      {children}
    </div>
  );
}

function AiAuditStepTable({ steps }: { steps: GravityAiAuditStep[] }) {
  return (
    <div style={{ overflowX: "auto", marginTop: 10 }}>
      <table style={{ width: "100%", fontSize: 12, minWidth: 420, borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={thL}>Step</th>
            <th style={thL}>Claude</th>
            <th style={thL}>Gemini</th>
            <th style={thL}>Notes</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={`${s.step_number ?? i}-${s.step_title}`}>
              <td style={tdL}>
                {s.step_number != null ? `${s.step_number}. ` : ""}
                {s.step_title}
                {s.disagreement && (
                  <span className="badge badge-warn" style={{ marginLeft: 6 }}>
                    ⚠ disagree
                  </span>
                )}
              </td>
              <td style={tdL}>{s.claude}</td>
              <td style={tdL}>{s.gemini}</td>
              <td style={{ ...tdL, color: theme.textMuted }}>{s.notes || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GravityAuditSection() {
  const { data, loading, error, status, reload } = useApi<GravityAuditStatus>(
    () => api.getGravityAuditStatus(),
    []
  );

  return (
    <section style={{ marginTop: 24 }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, margin: "0 0 4px" }}>🛡️ Gravity Audit</h2>
      <p style={{ color: theme.textMuted, fontSize: 12.5, lineHeight: 1.5, marginBottom: 12 }}>
        The platform's own structural + AI-cross-checked self-audit — read-only
        here; a new run is triggered from the desktop Command Center's Safety
        tab.
      </p>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <>
          {/* ---- AI Gravity audit runner ---- */}
          <div className="card card-pad" style={{ marginBottom: 12 }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 8,
                marginBottom: 10,
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 13.5 }}>AI Gravity Audit (Claude + Gemini)</div>
              <span className="chip">{data.ai_audit.status}</span>
            </div>

            {AI_STATUS_NOTE[data.ai_audit.status] && (
              <p style={{ color: theme.textSecondary, fontSize: 12.5, lineHeight: 1.5, marginBottom: 10 }}>
                {AI_STATUS_NOTE[data.ai_audit.status]}
              </p>
            )}

            <Banner {...AI_HEALTH_STYLE[data.ai_audit.health]}>{data.ai_audit.health_caption}</Banner>

            {data.ai_audit.total_steps > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
                <span className="chip">{data.ai_audit.total_steps} steps</span>
                <span className="chip">
                  Claude {data.ai_audit.claude_passed}✓ / {data.ai_audit.claude_failed}✗
                </span>
                <span className="chip">
                  Gemini {data.ai_audit.gemini_passed}✓ / {data.ai_audit.gemini_failed}✗
                </span>
                <span className="chip">{data.ai_audit.disagreements} disagreement(s)</span>
                <span className="chip">Last run {timeAgo(data.ai_audit.generated_at)}</span>
              </div>
            )}

            {data.ai_audit.steps.length > 0 && <AiAuditStepTable steps={data.ai_audit.steps} />}
          </div>

          {/* ---- Legacy structural Gravity Review Suite ---- */}
          <div className="card card-pad">
            <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 10 }}>
              Legacy Structural Audit
            </div>
            <p style={{ color: theme.textMuted, fontSize: 11.5, lineHeight: 1.5, marginBottom: 10 }}>
              Pandera schema conformance, lookahead-bias perturbation,
              signal-registry health, sizing/risk gates — no LLM calls.
            </p>
            {data.legacy_audit.available ? (
              <>
                <Banner
                  {...(data.legacy_audit.all_passed
                    ? AI_HEALTH_STYLE.clean
                    : AI_HEALTH_STYLE.fail)}
                >
                  {data.legacy_audit.all_passed
                    ? "✅ All steps passed on the last run."
                    : "❌ At least one step failed on the last run — not cleared for live."}
                </Banner>
                <div style={{ overflowX: "auto", marginTop: 10 }}>
                  <table style={{ width: "100%", fontSize: 12, minWidth: 320, borderCollapse: "collapse" }}>
                    <tbody>
                      {data.legacy_audit.steps.map((s) => (
                        <tr key={s.step}>
                          <td style={tdL}>{s.step}</td>
                          <td style={{ ...tdL, textAlign: "right" }}>
                            <span className={s.passed ? "badge badge-good" : "badge badge-bad"}>
                              {s.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : (
              <p style={{ color: theme.textSecondary, fontSize: 12.5, lineHeight: 1.5 }}>
                {data.legacy_audit.reason}
              </p>
            )}
          </div>
        </>
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

  // Live deployability-gate thresholds (GET /thresholds, session-cached) so the
  // footer summary and the stress-gate tooltip quote the SAME numbers the
  // per-row GateChip values are already compared against — never a hard-coded
  // literal that could drift from an operator-tuned validation/thresholds.py
  // gate. Mirrors TabGuide.tsx's own loadThresholds() usage pattern.
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);
  useEffect(() => {
    let alive = true;
    void loadThresholds().then((t) => {
      if (alive) setThresholds(t);
    });
    return () => {
      alive = false;
    };
  }, []);

  const summary = useMemo(() => {
    if (!data) return null;
    const evaluated = data.filter((r) => r.gates.length > 0);
    const deployableCount = evaluated.filter((r) => r.deployable === true).length;
    const noBacktestCount = data.length - evaluated.length;
    return { total: data.length, evaluated: evaluated.length, deployableCount, noBacktestCount };
  }, [data]);

  // Which metric every card's run-over-run sparkline plots — one screen-wide
  // selector rather than a per-card control, so switching it re-plots every
  // Pilot's trend at once. Defaults to DSR (the primary deployability metric,
  // matching this screen's pre-existing behavior before this selector shipped).
  const [trendMetric, setTrendMetric] = useState<TrendMetricKey>("dsr");
  const hasAnyTrend = !!data?.some((r) => r.trend.length >= 2);

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

      <TabGuide tabKey="strategy-health" />

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            No pilots in the catalog yet.
          </div>
        ) : (
          <>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 8,
                margin: "4px 0 14px",
              }}
            >
              {summary && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
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
              {hasAnyTrend && (
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: theme.textMuted }}>
                  Trend metric
                  <select
                    value={trendMetric}
                    onChange={(e) => setTrendMetric(e.target.value as TrendMetricKey)}
                    data-testid="trend-metric-select"
                    style={{
                      background: theme.surface2,
                      color: theme.textSecondary,
                      border: `1px solid ${theme.border}`,
                      borderRadius: 6,
                      padding: "4px 8px",
                      fontSize: 12,
                    }}
                  >
                    {(Object.keys(GATE_SHORT_LABEL) as TrendMetricKey[]).map((key) => (
                      <option key={key} value={key}>
                        {GATE_SHORT_LABEL[key]}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            {data.map((row) => (
              <HealthCard key={row.pilot_id} row={row} thresholds={thresholds} metric={trendMetric} />
            ))}
          </>
        )
      )}

      {!loading && !error && data && data.length > 0 && (
        <>
          <h2 style={{ fontSize: 15, margin: "24px 0 4px" }}>Cross-strategy validation</h2>
          <p style={{ margin: "0 0 14px", fontSize: 13, color: theme.textMuted }}>
            Every strategy <code>validation.harness</code> has validated, not just the
            ones above wired to a Pilot — plus the run-over-run trend and macro-regime
            timeline behind those numbers.
          </p>
          <ValidationTrend />
        </>
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
        Deployable requires PBO &lt; {fmtNum(thresholds?.pbo_max, 2)}, DSR &gt;{" "}
        {fmtNum(thresholds?.dsr_min, 2)}, net Sharpe &gt;{" "}
        {fmtNum(thresholds?.net_sharpe_min, 2)}, Max Drawdown &lt;{" "}
        {fmtPct(thresholds?.max_drawdown_max, 0, { fromFraction: true })} — plus a
        tail-scenario stress gate for options-selling strategies. Thresholds are
        never loosened to force a green badge.
      </p>

      <GravityAuditSection />
    </div>
  );
}
