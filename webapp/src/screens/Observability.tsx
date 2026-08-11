import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type {
  CircuitBreakerTrip,
  LogAggregation,
  LogAggregationEntry,
  LogLevel,
  ObservabilitySummary,
  PerfRange,
  RiskGateBlockEntry,
  SizingCapEvent,
  StrategyPnlRow,
  Thresholds,
} from "../api/types";
import { LOG_LEVELS } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, InfoTip, Input, Loading, Notice, Select, Table, Tile } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { glossaryDef } from "../help/helpContent";
import { loadThresholds } from "../help/thresholds";
import { deriveAttentionItems, type AttentionItem } from "../observabilityAttention";
import { RangeToggle } from "../components/RangeToggle";
import { DrawdownArea, PerfLine } from "../components/charts";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";
import MacroSentimentDashboard from "../components/MacroSentimentDashboard";

const HORIZONS: readonly number[] = [10, 30, 60, 90];

/** Local horizon toggle — mirrors RangeToggle's segmented-control look, but
 * for the four forecast horizons the pipeline actually forecasts (not worth
 * generalizing RangeToggle, which is typed specifically to PerfRange). */
function HorizonToggle({
  value,
  onChange,
}: {
  value: number;
  onChange: (h: number) => void;
}) {
  return (
    <div className="segmented" role="tablist" aria-label="Forecast horizon">
      {HORIZONS.map((h) => (
        <button
          key={h}
          role="tab"
          aria-selected={h === value}
          className={h === value ? "on" : ""}
          onClick={() => onChange(h)}
        >
          {h}d
        </button>
      ))}
    </div>
  );
}

/** RISK ON -> growth, RECESSION/CREDIT EVENT -> decline, everything else
 * (NEUTRAL, UNKNOWN, ...) -> caution. Never guesses at a regime that wasn't
 * actually persisted. */
function regimeColor(regime: string | null): string {
  if (!regime) return theme.textMuted;
  const r = regime.toUpperCase();
  if (r.includes("RISK ON")) return theme.growth;
  if (r.includes("RECESSION") || r.includes("CREDIT EVENT")) return theme.decline;
  return theme.caution;
}

/**
 * SectionHeading — optionally carries a `helpKey` into GLOSSARY (helpContent.ts)
 * rendered as a small "?" InfoTip next to the title. This is deliberately a
 * SECOND, per-section education surface alongside the page-top TabGuide panel
 * (not a replacement for it): TabGuide expands only on a screen's first-ever
 * visit and then stays collapsed forever (help/helpState.ts), so a section
 * whose meaning isn't obvious from its title alone — "Sizing cap-event audit
 * trail", "ETF volatility transmission" — needs an explanation that's always
 * reachable, not just on the first visit. `thresholds` is the same
 * `GET /thresholds` result TabGuide already loads (loadThresholds()'s
 * module-level cache dedups the extra fetch to nothing); omit it for a
 * heading with no `helpKey`.
 */
function SectionHeading({
  title,
  sub,
  helpKey,
  thresholds = null,
  id,
}: {
  title: string;
  sub?: string;
  helpKey?: string;
  thresholds?: Thresholds | null;
  /** Anchor id — lets observabilityAttention.ts's items scroll straight to
   * this section instead of leaving the operator to hunt for it. */
  id?: string;
}) {
  const def = helpKey ? glossaryDef(helpKey, thresholds) : undefined;
  return (
    <div id={id} style={{ marginTop: "var(--s-6)", marginBottom: "var(--s-2-5)", scrollMarginTop: "var(--s-4)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)" }}>
        <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>{title}</h2>
        {def && (
          <InfoTip
            ariaLabel={`What is ${title.toLowerCase()}?`}
            content={def}
            triggerStyle={{
              background: "none",
              border: `1px solid ${theme.border}`,
              borderRadius: "50%",
              width: 18,
              height: 18,
              lineHeight: "16px",
              fontSize: "var(--t-micro)",
              color: theme.textMuted,
              cursor: "pointer",
              padding: 0,
            }}
          >
            ?
          </InfoTip>
        )}
      </div>
      {sub && (
        <p style={{ margin: "var(--s-1) 0 0", color: theme.textMuted, fontSize: "var(--t-label)" }}>{sub}</p>
      )}
    </div>
  );
}

/**
 * AttentionStripSection — the first thing this screen renders. Answers "does
 * anything here need a look right now" without the operator scanning all
 * eleven-plus sections themselves. Backed entirely by `deriveAttentionItems`
 * (observabilityAttention.ts) — pure, synchronous, no new backend fields;
 * Dashboard.tsx runs the exact same function so the two screens can never
 * disagree about what's notable. Clicking an item scrolls to that section's
 * `id` below. The empty-list "All clear" case is an honest, explicit state —
 * never fabricated when `data` hasn't loaded (the caller only renders this
 * once `data` exists).
 */
function AttentionStripSection({ items }: { items: AttentionItem[] }) {
  const scrollTo = (anchor: string) => {
    // Optional-chained on the method itself, not just the element -- jsdom
    // (this codebase's test environment) implements no real layout and
    // doesn't define scrollIntoView at all (see AIChatInterface.tsx's
    // identical guard and LogStream.test.tsx's doc comment on the same gap).
    document.getElementById(anchor)?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  if (items.length === 0) {
    return (
      <div
        className="card card-pad"
        data-testid="attention-strip-clear"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-2)",
          borderLeft: `3px solid ${theme.growth}`,
          marginBottom: "var(--s-4)",
        }}
      >
        <span style={{ color: theme.growth, fontWeight: 700 }}>✓ All clear</span>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-label)" }}>
          Nothing below needs attention right now.
        </span>
      </div>
    );
  }

  return (
    <div
      className="card card-pad"
      data-testid="attention-strip"
      style={{ marginBottom: "var(--s-4)", borderLeft: `3px solid ${theme.decline}` }}
    >
      <div
        style={{
          fontSize: "var(--t-footnote)",
          color: theme.textMuted,
          marginBottom: "var(--s-2)",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        Needs attention
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-1-5)" }}>
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => scrollTo(item.anchor)}
            data-testid="attention-item"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--s-2)",
              background: "none",
              border: "none",
              padding: 0,
              cursor: "pointer",
              textAlign: "left",
              font: "inherit",
              color: theme.textPrimary,
            }}
          >
            <span className={`badge ${item.severity === "critical" ? "badge-bad" : "badge-warn"}`}>
              {item.severity === "critical" ? "CRITICAL" : "WARNING"}
            </span>
            <span style={{ fontSize: "var(--t-label)" }}>{item.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * MacroGateControl — the webapp port of the Streamlit Command Center's
 * Observability tab toggle (gui/panels/observability.py:131-195). Mirrors
 * KillSwitchToggle's UX exactly: a Toggle that opens a confirm Modal
 * requiring a typed reason (a fat-finger guard, NOT a security control — the
 * real gates are the command token and MACRO_GATE_WRITES_ENABLED, server-side).
 * A bare toggle-flip is NOT appropriate for a control this close to a genuine
 * risk-management kill switch (see CLAUDE.md's MACRO_REGIME_GATE_ENABLED
 * section: it vetoes new BUY orders during RECESSION/CREDIT EVENT regimes).
 *
 * Renders nothing when the current state is unknown (`macro_regime_gate_enabled
 * === null`, e.g. the writer omitted it that cycle) — never fabricates a
 * checked state for a value that wasn't actually persisted (CONSTRAINT #4).
 * The Toggle itself is disabled (with `macro_gate_writable_note` shown) when
 * the server has `MACRO_GATE_WRITES_ENABLED=false`, so the control degrades
 * honestly instead of silently 403-ing on click.
 */
function MacroGateControl({
  regime,
  onChanged,
}: {
  regime: ObservabilitySummary["regime"];
  onChanged: () => void;
}) {
  const [confirmKind, setConfirmKind] = useState<"enable" | "disable" | null>(null);
  const [inputReason, setInputReason] = useState("");
  const putMutation = useMutation((enabled: boolean, reason: string) =>
    api.putMacroGate(enabled, reason)
  );

  if (regime.macro_regime_gate_enabled === null) return null;

  const on = regime.macro_regime_gate_enabled;
  const writable = regime.macro_gate_writable;

  const openConfirm = (next: boolean) => {
    setInputReason("");
    setConfirmKind(next ? "enable" : "disable");
  };

  const confirmAction = async () => {
    if (confirmKind === null) return;
    await putMutation.run(confirmKind === "enable", inputReason);
    setConfirmKind(null);
    onChanged();
  };

  return (
    <div style={{ marginTop: "var(--s-2-5)" }}>
      <Toggle
        checked={on}
        onChange={openConfirm}
        label={on ? "Macro regime gate: ON" : "Macro regime gate: OFF"}
        disabled={!writable}
        pending={putMutation.pending}
      />
      {!on && (
        <p style={{ color: theme.caution, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
          Technical BUY signals run without a macro veto. Re-enable before going live.
        </p>
      )}
      {!writable && (
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1-5)" }}>
          {regime.macro_gate_writable_note}
        </p>
      )}
      {putMutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2)" }}>
          <span>⚠️</span>
          <span>{putMutation.error}</span>
        </Notice>
      )}

      {confirmKind && (
        <Modal
          ariaLabel={confirmKind === "disable" ? "Disable macro regime gate" : "Enable macro regime gate"}
          onClose={() => setConfirmKind(null)}
        >
          <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
            {confirmKind === "disable" ? "Disable macro regime gate?" : "Enable macro regime gate?"}
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
            {confirmKind === "disable"
              ? "Technical BUY signals will run without a veto during RECESSION/CREDIT EVENT regimes (Sahm Rule ≥ 0.5, VIX > 30, or HY OAS > 6%). Always re-enable before going live."
              : "Restores the autonomous macro veto — new BUY orders will be blocked during RECESSION/CREDIT EVENT regimes."}
          </p>
          <Input
            label="Reason"
            value={inputReason}
            onChange={(e) => setInputReason(e.target.value)}
            hint="Required."
          />
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button variant="neutral" onClick={() => setConfirmKind(null)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={confirmAction}
              disabled={!inputReason.trim()}
              pending={putMutation.pending}
              style={{ flex: 2 }}
            >
              {confirmKind === "disable" ? "Disable" : "Enable"}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function RegimeBadgeRow({
  regime,
  onChanged,
}: {
  regime: ObservabilitySummary["regime"];
  onChanged: () => void;
}) {
  if (regime.reason) {
    return <div className="empty" style={{ padding: "var(--s-4)" }}>{regime.reason}</div>;
  }
  const badges: { label: string; value: string }[] = [
    { label: "As of", value: timeAgo(regime.as_of) },
    { label: "Regime", value: regime.market_regime ?? "—" },
    { label: "VIX", value: fmtNum(regime.vix, 1) },
    { label: "Sahm Rule", value: fmtNum(regime.sahm_rule, 3) },
    { label: "HY OAS", value: regime.high_yield_oas == null ? "—" : `${fmtNum(regime.high_yield_oas, 2)}%` },
    { label: "10Y-2Y", value: regime.yield_curve == null ? "—" : `${fmtNum(regime.yield_curve, 2)}%` },
    {
      label: "HMM risk-on",
      value: regime.hmm_risk_on_probability == null ? "—" : fmtPct(regime.hmm_risk_on_probability, 0, { fromFraction: true }),
    },
  ];
  return (
    <>
      <div
        data-testid="regime-badges"
        style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", marginTop: "var(--s-3)" }}
      >
        {badges.map((b) => (
          <span
            key={b.label}
            className="chip"
            style={
              b.label === "Regime"
                ? { color: regimeColor(regime.market_regime), fontWeight: 700 }
                : undefined
            }
          >
            {b.label}: {b.value}
          </span>
        ))}
        {regime.kill_switch_active && (
          <span className="badge badge-bad">Kill switch ACTIVE</span>
        )}
      </div>
      <MacroGateControl regime={regime} onChanged={onChanged} />
    </>
  );
}

function ForecastSkillSection({
  skill,
}: {
  skill: ObservabilitySummary["forecast_skill"];
}) {
  if (skill.reason) {
    return <div className="empty" style={{ padding: "var(--s-5)" }}>{skill.reason}</div>;
  }
  const weights = Object.entries(skill.skill_weights).sort((a, b) => b[1] - a[1]);
  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile label="Pending" value={skill.pending} />
        <Tile label="Completed" value={skill.completed} />
        <Tile label="Window" value={`${skill.window_days}d`} />
        <Tile label="Min obs" value={skill.min_obs} />
      </div>
      {weights.length === 0 ? (
        <div className="empty" style={{ padding: "var(--s-4)" }}>
          No skill weights yet — not enough completed forecasts in the window.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
          {weights.map(([model, weight]) => (
            <div key={model} style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)" }}>
              <span style={{ width: 96, fontSize: "var(--t-label)", color: theme.textSecondary, flex: "0 0 auto" }}>
                {model}
              </span>
              <div style={{ flex: 1, height: 8, borderRadius: "var(--r-2xs)", background: theme.surface2, overflow: "hidden" }}>
                <div
                  style={{
                    width: `${Math.max(0, Math.min(1, weight)) * 100}%`,
                    height: "100%",
                    background: theme.accent,
                  }}
                />
              </div>
              <span className="num" style={{ width: 46, textAlign: "right", fontSize: "var(--t-label)" }}>
                {fmtPct(weight, 0, { fromFraction: true })}
              </span>
            </div>
          ))}
        </div>
      )}
      {skill.reliability_curve.length > 0 && (
        <div style={{ marginTop: "var(--s-4)", overflowX: "auto" }}>
          <Table style={{ fontSize: "var(--t-caption)" }}>
            <thead>
              <tr>
                <th>Model</th>
                <th className="num">Bin</th>
                <th className="num">Mean error</th>
                <th className="num">Count</th>
              </tr>
            </thead>
            <tbody>
              {skill.reliability_curve.map((bin, i) => (
                <tr key={i}>
                  <td>{bin.model_name}</td>
                  <td className="num">
                    {bin.bin_center == null ? "—" : fmtPct(bin.bin_center, 0, { fromFraction: true })}
                  </td>
                  <td className="num">
                    {bin.mean_pct_error == null ? "—" : fmtPct(bin.mean_pct_error, 1, { fromFraction: true, signed: true })}
                  </td>
                  <td className="num">{bin.count}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}

/**
 * ForecastSkillBySymbolSection — the per-symbol breakdown the portfolio-wide
 * ForecastSkillSection above doesn't carry (rank 7 of the published Mission
 * Control research: confirmed as wanted, previously only in the retired
 * Streamlit panel). One row per symbol from the last pipeline snapshot;
 * "Top model" is whichever model currently carries the largest inverse-RMSE
 * share for that symbol — a quick "what's driving this symbol's forecast"
 * read without needing all four models' weights spelled out per row. A
 * symbol with zero completed forecasts still gets a row (Pending/Completed
 * counts, "—" for the model) rather than being silently dropped — see
 * pilots/observability.py::forecast_skill_by_symbol_summary's own contract.
 */
function ForecastSkillBySymbolSection({
  bySymbol,
}: {
  bySymbol: ObservabilitySummary["forecast_skill_by_symbol"];
}) {
  if (bySymbol.reason) {
    return <div className="empty" style={{ padding: "var(--s-4)" }}>{bySymbol.reason}</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <Table style={{ fontSize: "var(--t-caption)" }}>
        <thead>
          <tr>
            <th>Symbol</th>
            <th className="num">Pending</th>
            <th className="num">Completed</th>
            <th>Top model</th>
            <th className="num">Weight</th>
          </tr>
        </thead>
        <tbody>
          {bySymbol.rows.map((row) => {
            const ranked = Object.entries(row.skill_weights).sort((a, b) => b[1] - a[1]);
            const top = ranked[0];
            return (
              <tr key={row.symbol} data-testid="forecast-skill-symbol-row">
                <td>{row.symbol}</td>
                <td className="num">{row.pending}</td>
                <td className="num">{row.completed}</td>
                <td>{top ? top[0] : "—"}</td>
                <td className="num">{top ? fmtPct(top[1], 0, { fromFraction: true }) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}

function BlockLogRow({ entry }: { entry: RiskGateBlockEntry }) {
  return (
    <div
      className="card card-pad"
      style={{ marginBottom: "var(--s-2)" }}
      data-testid="risk-gate-block-row"
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-2)" }}>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>
          {entry.symbol ?? "—"} {entry.side ? entry.side.toUpperCase() : ""}
          {entry.qty != null ? ` × ${fmtNum(entry.qty, 2)}` : ""}
        </span>
        <span style={{ fontSize: "var(--t-micro)", color: theme.textMuted, whiteSpace: "nowrap" }}>
          {entry.ts ? timeAgo(entry.ts) : "—"}
        </span>
      </div>
      <div style={{ fontSize: "var(--t-footnote)", color: theme.caution, marginTop: "var(--s-0-5)" }}>
        {entry.check ?? "—"}
        {entry.strategy_id ? ` · ${entry.strategy_id}` : ""}
      </div>
      {entry.reason && (
        <div style={{ fontSize: "var(--t-label)", color: theme.textSecondary, marginTop: "var(--s-1)", lineHeight: 1.4 }}>
          {entry.reason}
        </div>
      )}
    </div>
  );
}

/** Severity chip for one circuit-breaker trip. Reuses the existing
 * `badge`/`badge-bad`/`badge-warn` CSS classes already applied elsewhere on
 * this screen (e.g. the "Kill switch ACTIVE" badge above) rather than
 * inventing a new visual pattern. */
function SeverityBadge({ severity }: { severity: CircuitBreakerTrip["severity"] }) {
  return (
    <span className={`badge ${severity === "CRITICAL" ? "badge-bad" : "badge-warn"}`}>
      {severity}
    </span>
  );
}

/** One deduped, severity-classified circuit-breaker trip (gui/circuit_breakers.py,
 * ported from the legacy Streamlit Gravity Audit tab's merged kill-switch +
 * risk-gate-block dashboard). Distinct from BlockLogRow below: this is the
 * classified/deduped-within-window projection, not the raw JSONL tail. */
function CircuitBreakerRow({ trip }: { trip: CircuitBreakerTrip }) {
  return (
    <div className="card card-pad" style={{ marginBottom: "var(--s-2)" }} data-testid="circuit-breaker-row">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-2)" }}>
        <span style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <SeverityBadge severity={trip.severity} />
          <span style={{ fontWeight: 700, fontSize: 13.5 }}>{trip.name}</span>
        </span>
        <span style={{ fontSize: "var(--t-micro)", color: theme.textMuted, whiteSpace: "nowrap" }}>
          {trip.triggered_at ? timeAgo(trip.triggered_at) : "—"}
        </span>
      </div>
      <div style={{ fontSize: "var(--t-label)", color: theme.textSecondary, marginTop: "var(--s-1)", lineHeight: 1.4 }}>
        {trip.summary}
      </div>
      {(trip.threshold != null || trip.observed != null) && (
        <div style={{ fontSize: "var(--t-footnote)", color: theme.textMuted, marginTop: "var(--s-1)" }}>
          {trip.threshold != null && `Threshold: ${fmtNum(trip.threshold, 3)}`}
          {trip.threshold != null && trip.observed != null && " · "}
          {trip.observed != null && `Observed: ${fmtNum(trip.observed, 3)}`}
        </div>
      )}
    </div>
  );
}

/** KPI strip (Tile — the same primitive the Portfolio risk section above
 * already uses) + the trip list. Renders the honest empty state (no
 * fabricated "all clear" tile) when nothing is tripped. */
function CircuitBreakerSection({
  breakers,
}: {
  breakers: ObservabilitySummary["circuit_breakers"];
}) {
  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile
          label="Critical trips"
          value={breakers.counts.critical}
          tone={breakers.counts.critical > 0 ? "neg" : undefined}
        />
        <Tile
          label="Warning trips"
          value={breakers.counts.warning}
          tone={breakers.counts.warning > 0 ? "neg" : undefined}
        />
        <Tile label="Total" value={breakers.counts.total} />
      </div>
      {breakers.trips.length === 0 ? (
        <div className="empty" style={{ padding: "var(--s-4)" }}>
          {breakers.reason ?? "No active circuit-breaker trips."}
        </div>
      ) : (
        breakers.trips.map((t, i) => <CircuitBreakerRow key={`${t.name}-${i}`} trip={t} />)
      )}
    </div>
  );
}

/** Mirrors gui/observability_telemetry.py::format_bytes exactly (B/KiB/MiB/
 * GiB/TiB, one decimal). `null`/negative (the honest "couldn't sample"
 * sentinel — CONSTRAINT #4) renders "—", never "0 B". */
function fmtBytes(n: number | null): string {
  if (n == null || n < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  for (const u of units) {
    if (v < 1024) return `${v.toFixed(1)} ${u}`;
    v /= 1024;
  }
  return `${v.toFixed(1)} PiB`;
}

/**
 * SystemTelemetrySection — host + current-process CPU/memory/disk, the
 * webapp port of gui/panels/observability.py
 * ::_render_observability_system_telemetry. Point-in-time only (re-sampled
 * on every screen load, no history — see SystemTelemetry's doc comment in
 * types.ts). Reproduces the legacy panel's saturation cues (CPU >= 90% error,
 * >= 75% warning; memory >= 90% error) at the same thresholds.
 */
function SystemTelemetrySection({ telemetry }: { telemetry: ObservabilitySummary["system_telemetry"] }) {
  if (!telemetry.psutil_available) {
    return (
      <div className="empty" style={{ padding: "var(--s-4)" }}>
        {telemetry.reason ?? "psutil is not available — telemetry cannot be sampled."}
      </div>
    );
  }

  const cpuHot = telemetry.cpu_percent != null && telemetry.cpu_percent >= 90;
  const cpuWarm = !cpuHot && telemetry.cpu_percent != null && telemetry.cpu_percent >= 75;
  const memHot = telemetry.memory_percent != null && telemetry.memory_percent >= 90;

  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-2)" }}>
        <Tile label="Host CPU" value={fmtPct(telemetry.cpu_percent, 1)} tone={cpuHot ? "neg" : undefined} />
        <Tile label="Host memory" value={fmtPct(telemetry.memory_percent, 1)} tone={memHot ? "neg" : undefined} />
        <Tile label="Host disk" value={fmtPct(telemetry.disk_percent, 1)} />
        <Tile label="Process RSS" value={fmtBytes(telemetry.process_rss_bytes)} />
        <Tile label="Process CPU" value={fmtPct(telemetry.process_cpu_percent, 1)} />
        <Tile label="Threads" value={telemetry.process_threads ?? "—"} />
      </div>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}>
        Memory: {fmtBytes(telemetry.memory_used_bytes)} / {fmtBytes(telemetry.memory_total_bytes)}
        {" · "}Disk: {fmtBytes(telemetry.disk_used_bytes)} / {fmtBytes(telemetry.disk_total_bytes)}
        {telemetry.cpu_count_logical != null && ` · ${telemetry.cpu_count_logical} logical cores`}
        {telemetry.load_avg_1m != null && ` · Load avg (1m): ${fmtNum(telemetry.load_avg_1m, 2)}`}
      </p>
      {cpuHot && (
        <p style={{ color: theme.decline, fontSize: "var(--t-label)", marginTop: "var(--s-1)" }}>
          CPU saturated at {fmtPct(telemetry.cpu_percent, 0)} — strategy backtests may be queuing.
        </p>
      )}
      {cpuWarm && (
        <p style={{ color: theme.caution, fontSize: "var(--t-label)", marginTop: "var(--s-1)" }}>
          CPU at {fmtPct(telemetry.cpu_percent, 0)} — watch for slowdowns.
        </p>
      )}
      {memHot && (
        <p style={{ color: theme.decline, fontSize: "var(--t-label)", marginTop: "var(--s-1)" }}>
          Memory at {fmtPct(telemetry.memory_percent, 0)} — consider releasing caches.
        </p>
      )}
      <p style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-2)" }}>
        Sampled {telemetry.sampled_at ? timeAgo(telemetry.sampled_at) : "—"} — reload the screen to re-sample.
      </p>
    </div>
  );
}

/**
 * LatencyHeatmapSection — per-symbol quote fetch-to-ingestion latency
 * (rank 8 of the published Mission Control research: confirmed as wanted,
 * previously only in the retired Streamlit panel — and, unlike that panel,
 * recorded automatically rather than requiring a manual "Fetch quotes"
 * click). Samples live only in the API process's memory
 * (market_data_latency.py) and reset on every restart — the KPI strip and
 * "Samples since..." caption are worded the same honest, point-in-time way
 * SystemTelemetrySection's "reload the screen to re-sample" caption is.
 */
function LatencyHeatmapSection({
  latency,
}: {
  latency: ObservabilitySummary["latency_heatmap"];
}) {
  if (latency.reason) {
    return <div className="empty" style={{ padding: "var(--s-4)" }}>{latency.reason}</div>;
  }
  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile label="Samples" value={latency.count} />
        <Tile label="Median (p50)" value={latency.p50 == null ? "—" : `${fmtNum(latency.p50, 2)}s`} />
        <Tile label="p95" value={latency.p95 == null ? "—" : `${fmtNum(latency.p95, 2)}s`} />
        <Tile
          label="Worst symbol"
          value={latency.worst_symbol ?? "—"}
          tone={latency.worst_symbol ? "neg" : undefined}
        />
      </div>
      <div style={{ overflowX: "auto" }}>
        <Table style={{ fontSize: "var(--t-caption)" }}>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Source</th>
              <th className="num">Latency</th>
              <th>Stale</th>
              <th>Ingested</th>
            </tr>
          </thead>
          <tbody>
            {latency.rows.map((s, i) => (
              <tr key={`${s.symbol}-${i}`} data-testid="latency-sample-row">
                <td>{s.symbol}</td>
                <td>{s.source}</td>
                <td className="num">{fmtNum(s.latency_seconds, 2)}s</td>
                <td>
                  {s.is_stale ? (
                    <span className="badge badge-warn">stale</span>
                  ) : (
                    <span className="badge badge-neutral">fresh</span>
                  )}
                </td>
                <td>{timeAgo(s.ingested_at)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-2)" }}>
        Samples since this API process last started — never persisted to disk, so a restart clears this table.
      </p>
    </div>
  );
}

/**
 * SizingCapAuditSection — durable position-sizing guardrail events (last
 * ~100), the webapp port of gui/panels/observability.py
 * ::_render_observability_sizing_cap_audit. Distinct from the per-cycle
 * Sizing_Was_Capped column already surfaced elsewhere (e.g. Strategy
 * Matrix) -- this is the DURABLE cross-cycle history.
 */
function SizingCapAuditSection({
  audit,
}: {
  audit: ObservabilitySummary["sizing_cap_audit"];
}) {
  if (audit.events.length === 0) {
    return <div className="empty" style={{ padding: "var(--s-5)" }}>{audit.reason ?? "No cap events recorded yet."}</div>;
  }
  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile label="Events" value={audit.count} />
        <Tile label="Capped" value={audit.capped_count} tone={audit.capped_count > 0 ? "neg" : undefined} />
        <Tile
          label="Escalation"
          value={audit.escalation_enabled ? `ON (${audit.escalation_threshold_cycles}c × ${fmtNum(audit.escalation_factor, 2)})` : "OFF"}
        />
      </div>
      <div style={{ overflowX: "auto" }}>
        <Table style={{ fontSize: "var(--t-caption)" }}>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Strategy</th>
              <th className="num">Final weight</th>
              <th>Constraint</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {audit.events.map((e: SizingCapEvent) => (
              <tr key={e.id} data-testid="sizing-cap-event-row">
                <td>{e.symbol}</td>
                <td>{e.strategy_id ?? "—"}</td>
                <td className="num">{e.final_weight == null ? "—" : fmtPct(e.final_weight, 1, { fromFraction: true })}</td>
                <td>
                  {e.was_capped ? (
                    <span className="badge badge-warn">{e.binding_constraint ?? "capped"}</span>
                  ) : (
                    <span className="badge badge-neutral">not capped</span>
                  )}
                </td>
                <td>{e.timestamp ? timeAgo(e.timestamp) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  );
}

/**
 * EtfTransmissionSection — per-symbol ETF volatility-transmission diagnostic
 * (Ben-David, Franzoni & Moussawi 2018), the webapp port of
 * gui/panels/observability.py::_render_observability_etf_transmission. Three
 * independent master switches are shown even when rows is empty, so the
 * operator can distinguish "off" from "on but no coverage yet".
 */
function EtfTransmissionSection({
  etf,
}: {
  etf: ObservabilitySummary["etf_transmission"];
}) {
  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile label="Measurement" value={etf.measurement_enabled ? "ON" : "OFF"} />
        <Tile label="Sizing derate" value={etf.sizing_enabled ? "ON" : "OFF"} />
        <Tile label="Portfolio covariance" value={etf.portfolio_enabled ? "ON" : "OFF"} />
      </div>
      {etf.rows.length === 0 ? (
        <div className="empty" style={{ padding: "var(--s-4)" }}>{etf.reason ?? "No ETF-transmission coverage yet."}</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <Table style={{ fontSize: "var(--t-caption)" }}>
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">Ownership</th>
                <th className="num">Comovement R²</th>
                <th>Wrapper</th>
                <th className="num">Multiplier</th>
              </tr>
            </thead>
            <tbody>
              {etf.rows.map((r) => (
                <tr key={r.symbol} data-testid="etf-transmission-row">
                  <td>{r.symbol}</td>
                  <td className="num">{r.etf_ownership_pct == null ? "—" : fmtPct(r.etf_ownership_pct, 1, { fromFraction: true })}</td>
                  <td className="num">{fmtNum(r.etf_comovement_r2, 2)}</td>
                  <td>{r.etf_primary_wrapper ?? "—"}</td>
                  <td className="num">
                    {r.etf_transmission_multiplier == null
                      ? etf.sizing_enabled
                        ? "—"
                        : "N/A"
                      : `${fmtNum(r.etf_transmission_multiplier, 2)}x`}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}

/**
 * HeartbeatSection — current orchestrator heartbeat age + freshness label
 * ONLY. The legacy Streamlit panel's "Heartbeat Age Trend" sparkline has no
 * durable backing store (a session-only ring buffer) -- see
 * ObservabilitySummary["heartbeat"]'s doc comment in types.ts -- so this
 * deliberately renders a single current-value tile plus that honesty note,
 * never a fabricated one-point "trend".
 */
function HeartbeatSection({ heartbeat }: { heartbeat: ObservabilitySummary["heartbeat"] }) {
  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile
          label="Heartbeat age"
          value={heartbeat.age_seconds == null ? "—" : `${fmtNum(heartbeat.age_seconds, 0)}s`}
          tone={heartbeat.age_seconds != null && heartbeat.age_seconds > 120 ? "neg" : undefined}
        />
        <Tile label="Status" value={heartbeat.status ?? "—"} />
      </div>
      {heartbeat.reason && (
        <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>{heartbeat.reason}</p>
      )}
      <p style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-2)" }}>
        {heartbeat.history_note}
      </p>
    </div>
  );
}

/**
 * StrategyPnlSection — realized P&L grouped by strategy
 * (transactions_store.TransactionsStore), the FUNCTIONAL webapp port of the
 * legacy Streamlit "Strategy P&L" section (which is dead code against real
 * data server-side -- see pilots/observability.py::strategy_pnl_summary's
 * docstring). A `strategy_id: null` row (untagged trades) renders "Untagged",
 * never dropped -- it's real realized money either way.
 */
function StrategyPnlSection({ pnl }: { pnl: ObservabilitySummary["strategy_pnl"] }) {
  if (pnl.rows.length === 0) {
    return <div className="empty" style={{ padding: "var(--s-5)" }}>{pnl.reason ?? "No closed trades yet."}</div>;
  }
  return (
    <div>
      <Tile
        label="Total realized P&L"
        value={pnl.total_realized_pnl == null ? "—" : `$${fmtNum(pnl.total_realized_pnl, 2)}`}
        tone={pnl.total_realized_pnl != null ? (pnl.total_realized_pnl >= 0 ? "pos" : "neg") : undefined}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
        {pnl.rows.map((r: StrategyPnlRow) => (
          <div
            key={r.strategy_id ?? "__untagged__"}
            data-testid="strategy-pnl-row"
            className="card card-pad"
            style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}
          >
            <span style={{ fontWeight: 700, fontSize: 13.5 }}>{r.strategy_id ?? "Untagged"}</span>
            <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{r.trade_count} trade(s)</span>
            <span
              className="num"
              style={{ fontWeight: 700, color: (r.realized_pnl ?? 0) >= 0 ? theme.growth : theme.decline }}
            >
              {r.realized_pnl == null ? "—" : `$${fmtNum(r.realized_pnl, 2)}`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const LOG_LEVEL_ORDER: Record<LogLevel, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 4,
};

const LOG_LEVEL_OPTIONS: { value: LogLevel; label: string }[] = LOG_LEVELS.map((lvl) => ({
  value: lvl,
  label: lvl,
}));

function LogLevelBadge({ level }: { level: LogLevel | null }) {
  if (!level) return <span className="badge badge-neutral">—</span>;
  const cls =
    level === "CRITICAL" || level === "ERROR"
      ? "badge-bad"
      : level === "WARNING"
      ? "badge-warn"
      : "badge-neutral";
  return <span className={`badge ${cls}`}>{level}</span>;
}

function LogEntryRow({ entry }: { entry: LogAggregationEntry }) {
  return (
    <div
      data-testid="log-entry-row"
      style={{
        display: "flex",
        gap: "var(--s-2)",
        alignItems: "baseline",
        padding: "3px 0",
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
        fontSize: "var(--t-footnote)",
      }}
    >
      <span style={{ color: theme.textMuted, whiteSpace: "nowrap" }}>
        {entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : "—"}
      </span>
      <LogLevelBadge level={entry.level} />
      <span style={{ color: "#10b981", wordBreak: "break-word" }}>
        {entry.parsed ? entry.message : entry.raw}
      </span>
    </div>
  );
}

/**
 * LogAggregationSection — the webapp port of gui/panels/observability.py
 * ::_render_observability_error_log's core read path. The backend returns an
 * already-bounded, already-parsed batch (GET /observability/logs); level and
 * substring filtering happen entirely client-side over that fixed batch,
 * mirroring the legacy Streamlit panel's own UX (a selectbox/text_input
 * re-filters an already-fetched list on every rerun, not a fresh query per
 * keystroke).
 *
 * Deliberately omits the legacy panel's per-symbol "Contextual Error
 * Summary" message drilldown (grouped by ticker) — only the systemic/
 * symbol-specific COUNTS are shown, matching the backend's own
 * scope-narrowing decision (see pilots/observability.py::log_aggregation).
 */
function LogAggregationSection({ logs }: { logs: LogAggregation }) {
  const [minLevel, setMinLevel] = useState<LogLevel>("INFO");
  const [needle, setNeedle] = useState("");

  const filtered = useMemo(() => {
    const threshold = LOG_LEVEL_ORDER[minLevel];
    const q = needle.trim().toLowerCase();
    return logs.entries.filter((e) => {
      if (e.parsed && e.level && LOG_LEVEL_ORDER[e.level] < threshold) return false;
      if (q && !e.raw.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [logs.entries, minLevel, needle]);

  if (logs.entries.length === 0) {
    return (
      <div className="empty" style={{ padding: "var(--s-5)" }}>
        {logs.reason ?? "No log entries yet."}
      </div>
    );
  }

  return (
    <div>
      <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
        <Tile label="Critical" value={logs.tally.CRITICAL} tone={logs.tally.CRITICAL > 0 ? "neg" : undefined} />
        <Tile label="Error" value={logs.tally.ERROR} tone={logs.tally.ERROR > 0 ? "neg" : undefined} />
        <Tile label="Warning" value={logs.tally.WARNING} tone={logs.tally.WARNING > 0 ? "neg" : undefined} />
        <Tile label="Info" value={logs.tally.INFO} />
        <Tile label="Systemic" value={logs.systemic_count} tone={logs.systemic_count > 0 ? "neg" : undefined} />
        <Tile label="Symbol-specific" value={logs.symbol_specific_count} />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)", alignItems: "flex-end", marginBottom: "var(--s-2-5)" }}>
        <div style={{ minWidth: 140 }}>
          <Select
            label="Minimum level"
            value={minLevel}
            onChange={(e) => setMinLevel(e.target.value as LogLevel)}
            options={LOG_LEVEL_OPTIONS}
            testId="log-level-select"
          />
        </div>
        <div style={{ flex: 1, minWidth: 160 }}>
          <Input label="Filter (substring)" value={needle} onChange={(e) => setNeedle(e.target.value)} />
        </div>
      </div>

      <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", marginBottom: "var(--s-1-5)" }}>
        Showing {filtered.length} of {logs.returned_count} returned lines ({logs.total_lines} in the full tail).
      </p>

      {filtered.length === 0 ? (
        <div className="empty" style={{ padding: "var(--s-4)" }}>
          No log lines match the current filter.
        </div>
      ) : (
        <div
          style={{
            background: "#0b0e11",
            border: `1px solid ${theme.borderStrong}`,
            borderRadius: "var(--r-sm)",
            padding: "var(--s-3)",
            maxHeight: 320,
            overflowY: "auto",
            scrollBehavior: "smooth",
          }}
        >
          {filtered.map((e, i) => (
            <LogEntryRow key={`${e.timestamp ?? i}-${i}`} entry={e} />
          ))}
        </div>
      )}
    </div>
  );
}

export function Observability() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const [range, setRange] = useState<PerfRange>("1Y");
  const [horizon, setHorizon] = useState<number>(30);

  // The composite /observability read: attention items, equity, risk-gate
  // blocks, etc.
  const { data, loading, error, status, reload } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary(range, horizon),
    [range, horizon]
  );
  useAutoPoll(reload, "observability", { hasError: error != null });

  // The bounded log tail (GET /observability/logs). Kept separate from the
  // main /observability endpoint (which polls) because logs are relatively
  // heavy and usually only needed for ad-hoc forensic drilling, not for
  // driving an always-on dashboard's KPI strip.
  const {
    data: logsData,
    loading: logsLoading,
    error: logsError,
    status: logsStatus,
    reload: logsReload,
  } = useApi<LogAggregation>(api.getObservabilityLogs, []);

  // Sync with loadThresholds so the definitions loaded below match the LIVE
  // config, not some hard-coded value.
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);
  useEffect(() => {
    loadThresholds().then(setThresholds).catch(console.error);
  }, []);

  const attentionItems = useMemo(() => {
    if (!data || !thresholds) return [];
    return deriveAttentionItems(data);
  }, [data, thresholds]);

  if (loading && !data) {
    return <Loading />;
  }

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)" }}>
          <button className="btn btn-neutral" onClick={back} aria-label="Go back">
            ←
          </button>
          <div>
            <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>Mission Control</h1>
            <p className="screen-sub" style={{ marginBottom: 0 }}>
              Portfolio risk, guardrail events, and system telemetry.
            </p>
          </div>
        </div>
      </div>

      <TabGuide tabKey="observability" />

      {error ? (
        <ErrorState message={error} status={status} onRetry={reload} />
      ) : !data ? (
        <div className="empty" style={{ padding: "var(--s-5)" }}>
          Failed to load observability data.
        </div>
      ) : (
        <div style={{ marginTop: "var(--s-4)" }}>
          {/* Rendered above the fold, before the portfolio-risk/equity
              section below -- the screen's one "does anything need a
              look right now" answer plus its one real control (the macro-
              gate toggle; the ETF-transmission config link below is the
              other) must never be hidden behind the collapsed disclosure
              below. See AttentionStripSection's and MacroGateControl's doc
              comments for the full rationale. */}
          <AttentionStripSection items={attentionItems} />

          <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }} id="macro-gate">
            <SectionHeading title="Control · Macro regime gate" />
            <RegimeBadgeRow regime={data.regime} onChanged={reload} />
          </section>

          {/* Portfolio risk + equity are grouped together, above the
              collapsed disclosure below -- these two are the sections an
              operator most plausibly wants to compare against each other. */}
          <div style={{ marginBottom: "var(--s-4)" }}>
            <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
              <div key="portfolioRisk">
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                  <div className="drag-handle">
                    <SectionHeading id="portfolio-risk" title="Portfolio risk" sub="Over the full account equity history" />
                  </div>
                  <div style={{ padding: "0 var(--s-3) var(--s-3)", flex: 1, overflow: "auto" }}>
                    <div className="tiles" style={{ marginBottom: "var(--s-3)" }}>
                      <Tile label="Sharpe" value={fmtNum(data.portfolio_risk.sharpe_ratio, 2)} />
                      <Tile label="Calmar" value={fmtNum(data.portfolio_risk.calmar_ratio, 2)} />
                      <Tile
                        label="Max drawdown"
                        value={fmtPct(data.portfolio_risk.max_drawdown, 1, { fromFraction: true })}
                        tone={
                          data.portfolio_risk.max_drawdown != null && data.portfolio_risk.max_drawdown < 0
                            ? "neg"
                            : undefined
                        }
                      />
                      <Tile
                        label="Max DD duration"
                        value={
                          data.portfolio_risk.max_drawdown_duration_days == null
                            ? "—"
                            : `${fmtNum(data.portfolio_risk.max_drawdown_duration_days, 0)}d`
                        }
                      />
                      <Tile label="CAGR" value={fmtPct(data.portfolio_risk.cagr, 1, { fromFraction: true })} />
                      <Tile
                        label="Portfolio heat"
                        value={
                          data.portfolio_heat.heat_pct == null
                            ? "—"
                            : `${fmtPct(data.portfolio_heat.heat_pct, 1, { fromFraction: true })} / ${
                                data.portfolio_heat.max_portfolio_heat == null
                                  ? "—"
                                  : fmtPct(data.portfolio_heat.max_portfolio_heat, 0, { fromFraction: true })
                              }`
                        }
                        tone={data.portfolio_heat.over_limit ? "neg" : undefined}
                      />
                    </div>
                    {data.portfolio_risk.reason && (
                      <p style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
                        {data.portfolio_risk.reason}
                      </p>
                    )}
                    {data.portfolio_heat.reason && (
                      <p style={{ color: "var(--text-muted)", fontSize: "var(--t-caption)", marginTop: "var(--s-1)" }}>
                        Portfolio heat: {data.portfolio_heat.reason}
                      </p>
                    )}
                  </div>
                </section>
              </div>

              <div key="equity">
                <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
                  <div className="drag-handle">
                    <SectionHeading title="Equity & drawdown" />
                  </div>
                  <div style={{ padding: "0 var(--s-3) var(--s-3)", flex: 1, overflow: "auto" }}>
                    <div style={{ marginBottom: "var(--s-2-5)" }}>
                      <RangeToggle value={range} onChange={setRange} />
                    </div>
                    {data.equity_curve.points.length === 0 ? (
                      <div className="empty" style={{ padding: "var(--s-5)" }}>
                        {data.equity_curve.reason ?? "No account equity history yet."}
                      </div>
                    ) : (
                      <>
                        <PerfLine
                          data={data.equity_curve.points.map((p) => ({ date: p.date, value: p.equity }))}
                        />
                        <DrawdownArea data={data.equity_curve.points} />
                      </>
                    )}
                  </div>
                </section>
              </div>
            </div>
          </div>

          {/* Everything below is detail, not "does anything need a look" --
              AttentionStripSection above already answers that. Collapsed by
              default, in normal document flow rather than a grid item. A
              native <details> (this codebase's own established collapsible
              idiom) rather than a bespoke component: keyboard/screen-reader
              support for free. */}
          <details className="card" style={{ marginTop: "var(--s-4)" }} data-testid="background-telemetry">
            <summary
              style={{
                cursor: "pointer",
                userSelect: "none",
                padding: "var(--s-3) var(--s-4)",
                fontWeight: 700,
                fontSize: "var(--t-label)",
                color: theme.textSecondary,
              }}
            >
              Background telemetry — forecast detail, circuit breakers, sizing &amp; ETF risk, system health, and logs
            </summary>
            <div style={{ padding: "0 var(--s-4) var(--s-4)" }}>
              <SectionHeading title="Forecast skill" sub="Portfolio-wide reliability and weights" />
              <div style={{ marginBottom: "var(--s-2-5)" }}>
                <HorizonToggle value={horizon} onChange={setHorizon} />
              </div>
              <ForecastSkillSection skill={data.forecast_skill} />

              <SectionHeading title="Forecast skill by symbol" sub="Leading model per symbol" />
              <ForecastSkillBySymbolSection bySymbol={data.forecast_skill_by_symbol} />

              <SectionHeading id="circuit-breakers" title="Circuit breakers" sub="Kill switch + risk-gate blocks" helpKey="circuit breaker" thresholds={thresholds} />
              <CircuitBreakerSection breakers={data.circuit_breakers} />

              <SectionHeading id="risk-gate-blocks" title="Risk gate block log" sub="Blocked orders" />
              {data.risk_gate_blocks.entries.length === 0 ? (
                <div className="empty" style={{ padding: "var(--s-5)" }}>
                  {data.risk_gate_blocks.reason ?? "No blocked orders in the log."}
                </div>
              ) : (
                <div>
                  {data.risk_gate_blocks.entries.map((e, i) => (
                    <BlockLogRow key={`${e.ts ?? i}-${i}`} entry={e} />
                  ))}
                </div>
              )}

              <SectionHeading title="System telemetry" sub="Host & process resource usage" />
              <SystemTelemetrySection telemetry={data.system_telemetry} />

              <SectionHeading title="Data latency" sub="Fetch-to-ingestion latency" />
              <LatencyHeatmapSection latency={data.latency_heatmap} />

              <SectionHeading id="sizing-cap-audit" title="Sizing cap-event audit trail" helpKey="sizing cap" thresholds={thresholds} />
              <SizingCapAuditSection audit={data.sizing_cap_audit} />

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: "var(--s-2)" }}>
                <SectionHeading title="ETF volatility transmission" helpKey="etf transmission" thresholds={thresholds} />
                <Button
                  variant="primary"
                  onClick={() => nav("/settings/etf-transmission")}
                  style={{ marginBottom: "var(--s-2-5)", fontSize: "var(--t-caption)" }}
                >
                  ⚙ Configure
                </Button>
              </div>
              <EtfTransmissionSection etf={data.etf_transmission} />

              <SectionHeading id="heartbeat" title="Heartbeat" sub="Orchestrator liveness" />
              <HeartbeatSection heartbeat={data.heartbeat} />

              <SectionHeading title="Strategy P&L" />
              <StrategyPnlSection pnl={data.strategy_pnl} />

              <SectionHeading title="Logs" sub="investyo.log tail" />
              {logsLoading && <Loading lines={2} />}
              {!logsLoading && logsError && (
                <ErrorState message={logsError} status={logsStatus} onRetry={logsReload} />
              )}
              {!logsLoading && !logsError && logsData && <LogAggregationSection logs={logsData} />}

              <div style={{ marginTop: "var(--s-8)", marginBottom: "var(--s-4)" }}>
                <MacroSentimentDashboard />
              </div>
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
