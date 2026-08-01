import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type {
  AiDisagreementRow,
  AiDisagreementsResponse,
  CurvePoint,
  GravityAiAuditStep,
  GravityAuditStatus,
  StrategyHealthGate,
  StrategyHealthRow,
  Thresholds,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { DeployableBadge, ErrorState, InfoTip, Loading, Select, Table } from "../components/ui";
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

function getGateHeatmapStyle(gate: StrategyHealthGate): React.CSSProperties {
  if (gate.passed == null || gate.value == null) return {};
  const diff = gate.value - gate.threshold;
  const margin = diff / Math.abs(gate.threshold || 1);
  const normalizedMargin = gate.direction === "below" ? -margin : margin;
  
  // Create an explicit heatmap tint by scaling opacity based on performance
  if (normalizedMargin > 0) {
    // Passed: scale from 0.1 to 0.4 opacity of green
    const opacity = Math.min(0.4, 0.1 + normalizedMargin * 0.3);
    return { backgroundColor: `rgba(16, 185, 129, ${opacity})`, borderColor: `rgba(16, 185, 129, ${opacity + 0.2})` };
  } else {
    // Failed: scale from 0.1 to 0.4 opacity of red
    const opacity = Math.min(0.4, 0.1 + Math.abs(normalizedMargin) * 0.3);
    return { backgroundColor: `rgba(239, 68, 68, ${opacity})`, borderColor: `rgba(239, 68, 68, ${opacity + 0.2})` };
  }
}

function GateChip({ gate }: { gate: StrategyHealthGate }) {
  const cls =
    gate.passed == null ? "badge badge-neutral" : gate.passed ? "badge badge-good" : "badge badge-bad";
  const valueStr = gate.value == null ? "—" : formatGateNumber(gate, gate.value);
  const heatmapStyle = getGateHeatmapStyle(gate);
  
  return (
    <InfoTip triggerClassName={cls} triggerStyle={heatmapStyle} content={gate.label}>
      {GATE_SHORT_LABEL[gate.key]} {valueStr}{" "}
      <span style={{ opacity: 0.75 }}>
        ({directionGlyph(gate.direction)} {formatGateNumber(gate, gate.threshold)})
      </span>
    </InfoTip>
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
    <InfoTip
      triggerClassName={passed ? "badge badge-good" : "badge badge-bad"}
      content={`Tail-scenario stress gate: survives OCT 2008 / FEB 2018 / MAR 2020 / AUG 2024 with < ${ddText} drawdown`}
    >
      Stress {passed ? "✓ passed" : "✗ failed"}
    </InfoTip>
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

const TREND_METRIC_OPTIONS: { value: TrendMetricKey; label: string }[] = (
  Object.keys(GATE_SHORT_LABEL) as TrendMetricKey[]
).map((key) => ({ value: key, label: GATE_SHORT_LABEL[key] }));

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
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-2)" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: "var(--t-subhead)", wordBreak: "break-word" }}>
            {row.pilot_name}
          </div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)" }}>
            {row.strategy_id ? `backtest: ${row.strategy_id}` : "no backtest joined"}
          </div>
        </div>
        <DeployableBadge deployable={row.deployable} />
      </div>

      {hasGates ? (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "var(--s-2)", marginTop: "var(--s-3)", flex: 1 }}>
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
            <div style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-2)" }}>
              Report date {row.report_date}
            </div>
          )}
          {curve.length >= 2 && (
            <div style={{ marginTop: "var(--s-2-5)" }}>
              <div style={{ fontSize: "var(--t-micro)", color: theme.textMuted, marginBottom: "var(--s-0-5)" }}>
                {GATE_SHORT_LABEL[metric]}, last {curve.length} runs
              </div>
              <Sparkline data={curve} positive={trendingBetter} />
            </div>
          )}
        </>
      ) : (
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", lineHeight: 1.5, marginTop: "var(--s-3)" }}>
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
        padding: "var(--s-2-5) var(--s-3)",
        fontSize: "var(--t-label)",
        lineHeight: 1.45,
      }}
    >
      {children}
    </div>
  );
}

function AiAuditStepTable({ steps }: { steps: GravityAiAuditStep[] }) {
  return (
    <div style={{ overflowX: "auto", marginTop: "var(--s-2-5)" }}>
      <Table style={{ fontSize: "var(--t-caption)", minWidth: 420 }}>
        <thead>
          <tr>
            <th>Step</th>
            <th>Claude</th>
            <th>Gemini</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={`${s.step_number ?? i}-${s.step_title}`}>
              <td style={{ verticalAlign: "top" }}>
                {s.step_number != null ? `${s.step_number}. ` : ""}
                {s.step_title}
                {s.disagreement && (
                  <span className="badge badge-warn" style={{ marginLeft: 6 }}>
                    ⚠ disagree
                  </span>
                )}
              </td>
              <td style={{ verticalAlign: "top" }}>{s.claude}</td>
              <td style={{ verticalAlign: "top" }}>{s.gemini}</td>
              <td style={{ verticalAlign: "top", color: theme.textMuted }}>{s.notes || "—"}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

/**
 * AiDisagreementSection — G15: durable per-symbol Claude-vs-Gemini verdict
 * comparison (GET /data/ai/disagreements). Distinct from the AI Gravity
 * Audit card's `disagreements` chip above (a durably-computed AGGREGATE
 * COUNT from the STRUCTURAL Gravity audit's own Claude/Gemini cross-check)
 * -- this is a per-symbol table sourced from REAL analyst-note/chart-pattern
 * calls, a different question entirely ("where do the two AI features
 * disagree on a symbol's trend", not "did the audit steps disagree").
 *
 * This is the durable equivalent of the legacy Streamlit AI Insights tab's
 * "Aggregate Claude vs Gemini disagreement" table, which is built from two
 * st.session_state mirrors and has no cross-session durable form -- see
 * api/data_api.py::get_ai_disagreements's docstring for the full honesty
 * note. `claude_verdict`/`gemini_verdict` are `null` -- never fabricated --
 * whenever that side has never been generated for the symbol.
 */
function DisagreementRowView({ row }: { row: AiDisagreementRow }) {
  return (
    <tr data-testid="ai-disagreement-row">
      <td style={{ fontWeight: 700 }}>
        {row.symbol}
        {row.disagreement && (
          <span className="badge badge-warn" style={{ marginLeft: 6 }}>
            ⚠ disagree
          </span>
        )}
      </td>
      <td style={{ color: theme.textMuted }}>{row.advisory_action}</td>
      <td>{row.claude_verdict ?? "—"}</td>
      <td>{row.gemini_verdict ?? "—"}</td>
    </tr>
  );
}

function AiDisagreementSection() {
  const { data, loading, error, status, reload } = useApi<AiDisagreementsResponse>(
    () => api.getAiDisagreements(),
    []
  );

  return (
    <section style={{ marginTop: "var(--s-6)" }}>
      <h2 style={{ fontSize: "var(--t-subhead)", fontWeight: 700, margin: "0 0 var(--s-1)" }}>
        🔍 AI Verdict Disagreements
      </h2>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", lineHeight: 1.5, marginBottom: "var(--s-3)" }}>
        Per-symbol Claude analyst note vs. Gemini chart-pattern read, from
        cached results in <code>output/llm_commentary_cache.json</code> — a
        durable record, not a per-session snapshot. Generate notes/reads from
        a symbol's detail page to populate this table.
      </p>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.rows.length === 0 ? (
          <div className="empty" style={{ padding: "var(--s-5)" }}>
            {data.reason ?? "No cached Claude/Gemini verdicts yet."}
          </div>
        ) : (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginBottom: "var(--s-3)" }}>
              <span className="chip">{data.summary.total_symbols} symbols</span>
              <span className="chip">{data.summary.both_present} with both verdicts</span>
              <span className="chip">{data.summary.agreements} agree</span>
              <span className="chip">{data.summary.disagreements} disagree</span>
            </div>
            <div style={{ overflowX: "auto" }}>
              <Table style={{ fontSize: "var(--t-caption)", minWidth: 420 }}>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Action</th>
                    <th>Claude</th>
                    <th>Gemini</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <DisagreementRowView key={r.symbol} row={r} />
                  ))}
                </tbody>
              </Table>
            </div>
          </>
        )
      )}
    </section>
  );
}

function GravityAuditSection() {
  const { data, loading, error, status, reload } = useApi<GravityAuditStatus>(
    () => api.getGravityAuditStatus(),
    []
  );

  return (
    <section style={{ marginTop: "var(--s-6)" }}>
      <h2 style={{ fontSize: "var(--t-subhead)", fontWeight: 700, margin: "0 0 var(--s-1)" }}>🛡️ Gravity Audit</h2>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-label)", lineHeight: 1.5, marginBottom: "var(--s-3)" }}>
        The platform's own structural + AI-cross-checked self-audit — read-only
        here; a new run is triggered from the desktop Command Center's Safety
        tab.
      </p>

      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <>
          {/* ---- AI Gravity audit runner ---- */}
          <div className="card card-pad" style={{ marginBottom: "var(--s-3)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "var(--s-2)",
                marginBottom: "var(--s-2-5)",
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 13.5 }}>AI Gravity Audit (Claude + Gemini)</div>
              <span className="chip">{data.ai_audit.status}</span>
            </div>

            {AI_STATUS_NOTE[data.ai_audit.status] && (
              <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", lineHeight: 1.5, marginBottom: "var(--s-2-5)" }}>
                {AI_STATUS_NOTE[data.ai_audit.status]}
              </p>
            )}

            <Banner {...AI_HEALTH_STYLE[data.ai_audit.health]}>{data.ai_audit.health_caption}</Banner>

            {data.ai_audit.total_steps > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
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
            <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: "var(--s-2-5)" }}>
              Legacy Structural Audit
            </div>
            <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", lineHeight: 1.5, marginBottom: "var(--s-2-5)" }}>
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
                <div style={{ overflowX: "auto", marginTop: "var(--s-2-5)" }}>
                  <Table style={{ fontSize: "var(--t-caption)", minWidth: 320 }}>
                    <tbody>
                      {data.legacy_audit.steps.map((s) => (
                        <tr key={s.step}>
                          <td style={{ verticalAlign: "top" }}>{s.step}</td>
                          <td className="num" style={{ verticalAlign: "top" }}>
                            <span className={s.passed ? "badge badge-good" : "badge badge-bad"}>
                              {s.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </>
            ) : (
              <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", lineHeight: 1.5 }}>
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
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
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
          <div className="empty" style={{ padding: "var(--s-7-5)" }}>
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
                gap: "var(--s-2)",
                margin: "var(--s-1) 0 var(--s-3-5)",
              }}
            >
              {summary && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)" }}>
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
                <div style={{ minWidth: 120 }}>
                  <Select
                    label="Trend metric"
                    value={trendMetric}
                    onChange={(e) => setTrendMetric(e.target.value as TrendMetricKey)}
                    options={TREND_METRIC_OPTIONS}
                    testId="trend-metric-select"
                  />
                </div>
              )}
            </div>
            <div className="dashboard-grid">
              {data.map((row) => (
                <HealthCard key={row.pilot_id} row={row} thresholds={thresholds} metric={trendMetric} />
              ))}
            </div>
          </>
        )
      )}

      {!loading && !error && data && data.length > 0 && (
        <>
          <h2 style={{ fontSize: "var(--t-subhead)", margin: "var(--s-6) 0 var(--s-1)" }}>Cross-strategy validation</h2>
          <p style={{ margin: "0 0 var(--s-3-5)", fontSize: "var(--t-body)", color: theme.textMuted }}>
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
          fontSize: "var(--t-footnote)",
          marginTop: "var(--s-5)",
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
      <AiDisagreementSection />
    </div>
  );
}
