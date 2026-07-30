import { useMemo, useState } from "react";
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
} from "../api/types";
import { LOG_LEVELS } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading, Notice, Select, Table, Tile } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { RangeToggle } from "../components/RangeToggle";
import { DrawdownArea, PerfLine } from "../components/charts";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
import { fmtNum, fmtPct, timeAgo } from "../format";
import { theme } from "../theme";

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

function SectionHeading({ title, sub }: { title: string; sub?: string }) {
  return (
    <div style={{ marginTop: 24, marginBottom: 10 }}>
      <h2 style={{ margin: 0, fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && (
        <p style={{ margin: "4px 0 0", color: theme.textMuted, fontSize: 12.5 }}>{sub}</p>
      )}
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
    <div style={{ marginTop: 10 }}>
      <Toggle
        checked={on}
        onChange={openConfirm}
        label={on ? "Macro regime gate: ON" : "Macro regime gate: OFF"}
        disabled={!writable}
        pending={putMutation.pending}
      />
      {!on && (
        <p style={{ color: theme.caution, fontSize: 12, marginTop: 6 }}>
          Technical BUY signals run without a macro veto. Re-enable before going live.
        </p>
      )}
      {!writable && (
        <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 6 }}>
          {regime.macro_gate_writable_note}
        </p>
      )}
      {putMutation.error && (
        <Notice variant="warn" style={{ marginTop: 8 }}>
          <span>⚠️</span>
          <span>{putMutation.error}</span>
        </Notice>
      )}

      {confirmKind && (
        <Modal
          ariaLabel={confirmKind === "disable" ? "Disable macro regime gate" : "Enable macro regime gate"}
          onClose={() => setConfirmKind(null)}
        >
          <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>
            {confirmKind === "disable" ? "Disable macro regime gate?" : "Enable macro regime gate?"}
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
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
          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
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
    return <div className="empty" style={{ padding: 16 }}>{regime.reason}</div>;
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
        style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}
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
    return <div className="empty" style={{ padding: 20 }}>{skill.reason}</div>;
  }
  const weights = Object.entries(skill.skill_weights).sort((a, b) => b[1] - a[1]);
  return (
    <div>
      <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
        <Tile label="Pending" value={skill.pending} />
        <Tile label="Completed" value={skill.completed} />
        <Tile label="Window" value={`${skill.window_days}d`} />
        <Tile label="Min obs" value={skill.min_obs} />
      </div>
      {weights.length === 0 ? (
        <div className="empty" style={{ padding: 16 }}>
          No skill weights yet — not enough completed forecasts in the window.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {weights.map(([model, weight]) => (
            <div key={model} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ width: 96, fontSize: 12.5, color: theme.textSecondary, flex: "0 0 auto" }}>
                {model}
              </span>
              <div style={{ flex: 1, height: 8, borderRadius: 4, background: theme.surface2, overflow: "hidden" }}>
                <div
                  style={{
                    width: `${Math.max(0, Math.min(1, weight)) * 100}%`,
                    height: "100%",
                    background: theme.accent,
                  }}
                />
              </div>
              <span className="num" style={{ width: 46, textAlign: "right", fontSize: 12.5 }}>
                {fmtPct(weight, 0, { fromFraction: true })}
              </span>
            </div>
          ))}
        </div>
      )}
      {skill.reliability_curve.length > 0 && (
        <div style={{ marginTop: 16, overflowX: "auto" }}>
          <Table style={{ fontSize: 12 }}>
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

function BlockLogRow({ entry }: { entry: RiskGateBlockEntry }) {
  return (
    <div
      className="card card-pad"
      style={{ marginBottom: 8 }}
      data-testid="risk-gate-block-row"
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>
          {entry.symbol ?? "—"} {entry.side ? entry.side.toUpperCase() : ""}
          {entry.qty != null ? ` × ${fmtNum(entry.qty, 2)}` : ""}
        </span>
        <span style={{ fontSize: 11, color: theme.textMuted, whiteSpace: "nowrap" }}>
          {entry.ts ? timeAgo(entry.ts) : "—"}
        </span>
      </div>
      <div style={{ fontSize: 11.5, color: theme.caution, marginTop: 2 }}>
        {entry.check ?? "—"}
        {entry.strategy_id ? ` · ${entry.strategy_id}` : ""}
      </div>
      {entry.reason && (
        <div style={{ fontSize: 12.5, color: theme.textSecondary, marginTop: 4, lineHeight: 1.4 }}>
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
    <div className="card card-pad" style={{ marginBottom: 8 }} data-testid="circuit-breaker-row">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <SeverityBadge severity={trip.severity} />
          <span style={{ fontWeight: 700, fontSize: 13.5 }}>{trip.name}</span>
        </span>
        <span style={{ fontSize: 11, color: theme.textMuted, whiteSpace: "nowrap" }}>
          {trip.triggered_at ? timeAgo(trip.triggered_at) : "—"}
        </span>
      </div>
      <div style={{ fontSize: 12.5, color: theme.textSecondary, marginTop: 4, lineHeight: 1.4 }}>
        {trip.summary}
      </div>
      {(trip.threshold != null || trip.observed != null) && (
        <div style={{ fontSize: 11.5, color: theme.textMuted, marginTop: 4 }}>
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
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
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
        <div className="empty" style={{ padding: 16 }}>
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
      <div className="empty" style={{ padding: 16 }}>
        {telemetry.reason ?? "psutil is not available — telemetry cannot be sampled."}
      </div>
    );
  }

  const cpuHot = telemetry.cpu_percent != null && telemetry.cpu_percent >= 90;
  const cpuWarm = !cpuHot && telemetry.cpu_percent != null && telemetry.cpu_percent >= 75;
  const memHot = telemetry.memory_percent != null && telemetry.memory_percent >= 90;

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 8 }}>
        <Tile label="Host CPU" value={fmtPct(telemetry.cpu_percent, 1)} tone={cpuHot ? "neg" : undefined} />
        <Tile label="Host memory" value={fmtPct(telemetry.memory_percent, 1)} tone={memHot ? "neg" : undefined} />
        <Tile label="Host disk" value={fmtPct(telemetry.disk_percent, 1)} />
        <Tile label="Process RSS" value={fmtBytes(telemetry.process_rss_bytes)} />
        <Tile label="Process CPU" value={fmtPct(telemetry.process_cpu_percent, 1)} />
        <Tile label="Threads" value={telemetry.process_threads ?? "—"} />
      </div>
      <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 4 }}>
        Memory: {fmtBytes(telemetry.memory_used_bytes)} / {fmtBytes(telemetry.memory_total_bytes)}
        {" · "}Disk: {fmtBytes(telemetry.disk_used_bytes)} / {fmtBytes(telemetry.disk_total_bytes)}
        {telemetry.cpu_count_logical != null && ` · ${telemetry.cpu_count_logical} logical cores`}
        {telemetry.load_avg_1m != null && ` · Load avg (1m): ${fmtNum(telemetry.load_avg_1m, 2)}`}
      </p>
      {cpuHot && (
        <p style={{ color: theme.decline, fontSize: 12.5, marginTop: 4 }}>
          CPU saturated at {fmtPct(telemetry.cpu_percent, 0)} — strategy backtests may be queuing.
        </p>
      )}
      {cpuWarm && (
        <p style={{ color: theme.caution, fontSize: 12.5, marginTop: 4 }}>
          CPU at {fmtPct(telemetry.cpu_percent, 0)} — watch for slowdowns.
        </p>
      )}
      {memHot && (
        <p style={{ color: theme.decline, fontSize: 12.5, marginTop: 4 }}>
          Memory at {fmtPct(telemetry.memory_percent, 0)} — consider releasing caches.
        </p>
      )}
      <p style={{ color: theme.textMuted, fontSize: 11, marginTop: 8 }}>
        Sampled {telemetry.sampled_at ? timeAgo(telemetry.sampled_at) : "—"} — reload the screen to re-sample.
      </p>
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
        gap: 8,
        alignItems: "baseline",
        padding: "3px 0",
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
        fontSize: 11.5,
      }}
    >
      <span style={{ color: theme.textMuted, whiteSpace: "nowrap" }}>
        {entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : "—"}
      </span>
      <LogLevelBadge level={entry.level} />
      <span style={{ color: theme.textSecondary, wordBreak: "break-word" }}>
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
      <div className="empty" style={{ padding: 20 }}>
        {logs.reason ?? "No log entries yet."}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
        <Tile label="Critical" value={logs.tally.CRITICAL} tone={logs.tally.CRITICAL > 0 ? "neg" : undefined} />
        <Tile label="Error" value={logs.tally.ERROR} tone={logs.tally.ERROR > 0 ? "neg" : undefined} />
        <Tile label="Warning" value={logs.tally.WARNING} tone={logs.tally.WARNING > 0 ? "neg" : undefined} />
        <Tile label="Info" value={logs.tally.INFO} />
        <Tile label="Systemic" value={logs.systemic_count} tone={logs.systemic_count > 0 ? "neg" : undefined} />
        <Tile label="Symbol-specific" value={logs.symbol_specific_count} />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end", marginBottom: 10 }}>
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

      <p style={{ color: theme.textMuted, fontSize: 11.5, marginBottom: 6 }}>
        Showing {filtered.length} of {logs.returned_count} returned lines ({logs.total_lines} in the full tail).
      </p>

      {filtered.length === 0 ? (
        <div className="empty" style={{ padding: 16 }}>
          No log lines match the current filter.
        </div>
      ) : (
        <div
          style={{
            background: theme.surface,
            border: `1px solid ${theme.border}`,
            borderRadius: 6,
            padding: "6px 10px",
            maxHeight: 320,
            overflowY: "auto",
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

  const { data, loading, error, status, reload } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary(range, horizon),
    [range, horizon]
  );

  // Kept as a SEPARATE fetch (not folded into `data` above) — mirrors the
  // backend's own GET /observability/logs split: a log tail is a heavier,
  // independently-loading payload, not one of the cheap composite sections.
  const {
    data: logsData,
    loading: logsLoading,
    error: logsError,
    status: logsStatus,
    reload: logsReload,
  } = useApi<LogAggregation>(() => api.getObservabilityLogs(300), []);

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
      <h1 className="screen-title">Mission Control</h1>
      <p className="screen-sub">
        Account risk stats, the equity curve, the macro regime, forecast
        skill, circuit breakers, blocked orders, host telemetry, and the log
        tail — one read-only view over what the engine already computed.
      </p>

      <TabGuide tabKey="observability" />

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}

      {!loading && !error && data && (
        <>
          {/* 1. Portfolio risk metrics */}
          <SectionHeading title="Portfolio risk" sub="Over the full account equity history" />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
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
            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 8 }}>
              {data.portfolio_risk.reason}
            </p>
          )}
          {data.portfolio_heat.reason && (
            <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 4 }}>
              Portfolio heat: {data.portfolio_heat.reason}
            </p>
          )}

          {/* 2. Equity + drawdown + regime overlay */}
          <SectionHeading title="Equity, drawdown &amp; regime" />
          <div style={{ marginBottom: 10 }}>
            <RangeToggle value={range} onChange={setRange} />
          </div>
          {data.equity_curve.points.length === 0 ? (
            <div className="empty" style={{ padding: 20 }}>
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
          <RegimeBadgeRow regime={data.regime} onChanged={reload} />

          {/* 3. Forecast skill (portfolio-wide) */}
          <SectionHeading
            title="Forecast skill"
            sub="Portfolio-wide reliability and inverse-RMSE model weights"
          />
          <div style={{ marginBottom: 10 }}>
            <HorizonToggle value={horizon} onChange={setHorizon} />
          </div>
          <ForecastSkillSection skill={data.forecast_skill} />

          {/* 4. Circuit breakers — merged kill-switch + risk-gate-block
              severity dashboard: deduped within a rolling window, classified
              CRITICAL/WARNING, with a KPI strip up top. */}
          <SectionHeading
            title="Circuit breakers"
            sub={`Deduped trips in the last ${data.circuit_breakers.window_hours}h — kill switch + risk-gate blocks, by severity`}
          />
          <CircuitBreakerSection breakers={data.circuit_breakers} />

          {/* 5. Risk gate block log (raw, undeduped JSONL tail) */}
          <SectionHeading
            title="Risk gate block log"
            sub={`Last ${data.risk_gate_blocks.count} blocked order(s), raw log`}
          />
          {data.risk_gate_blocks.entries.length === 0 ? (
            <div className="empty" style={{ padding: 20 }}>
              {data.risk_gate_blocks.reason ?? "No blocked orders in the log."}
            </div>
          ) : (
            <div style={{ maxHeight: 340, overflowY: "auto" }}>
              {data.risk_gate_blocks.entries.map((e, i) => (
                <BlockLogRow key={`${e.ts ?? i}-${i}`} entry={e} />
              ))}
            </div>
          )}

          {/* 6. System telemetry — host + current-process CPU/memory/disk.
              Point-in-time only, re-sampled on every load (no history). */}
          <SectionHeading
            title="System telemetry"
            sub="Host and process resource usage — reload the screen to re-sample"
          />
          <SystemTelemetrySection telemetry={data.system_telemetry} />

          {/* 7. Log aggregation — bounded, parsed tail of logs/investyo.log.
              Its own fetch (GET /observability/logs), independent of the
              composite above — see LogAggregationSection's doc comment. */}
          <SectionHeading
            title="Logs"
            sub="Tail of logs/investyo.log — filter by level and free text below"
          />
          {logsLoading && <Loading lines={2} />}
          {!logsLoading && logsError && (
            <ErrorState message={logsError} status={logsStatus} onRetry={logsReload} />
          )}
          {!logsLoading && !logsError && logsData && <LogAggregationSection logs={logsData} />}
        </>
      )}
    </div>
  );
}
