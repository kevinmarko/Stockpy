import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as ChartTooltip,
} from "recharts";
import { api } from "../api/client";
import type {
  MetaLabelDistribution,
  StrategyMatrix as StrategyMatrixT,
  StrategyModuleRow,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, InfoTip, Loading, Notice } from "../components/ui";
import { DynamicGrid, resetGridLayout } from "../components/DynamicGrid";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
import { chartAxisLine, chartAxisTick, chartGridProps, chartTooltipStyle } from "../components/charts";
import { fmtNum, timeAgo } from "../format";
import { theme } from "../theme";

/**
 * Strategy Matrix — read + (behind STRATEGY_WRITES_ENABLED) edit signal-module
 * weights and enabled/disabled state. A `.env`-write surface, so it lives under
 * /settings, reached from the "Signal modules" card.
 *
 * Honesty: an `.env` write does NOT reach the running process (settings is a
 * process-lifetime singleton), so after a successful Save the screen shows a
 * "restart to apply" notice and does NOT revert or re-fetch — the server itself
 * still reports the OLD values via env_drift.detected until restart. When
 * `writable` is false the inputs are disabled and Save is hidden.
 */

interface EditState {
  weights: Record<string, string>; // string-backed for the number inputs
  disabled: Set<string>;
}

function initEdit(modules: StrategyModuleRow[], disabled: string[]): EditState {
  const weights: Record<string, string> = {};
  for (const m of modules) {
    // weight is null only for a snapshot-only module (never, in practice, since
    // the union is exact) — default it to 0 so a Save still covers every module.
    weights[m.name] = String(m.weight ?? 0);
  }
  return { weights, disabled: new Set(disabled) };
}

function parseWeight(v: string): number | null {
  const n = Number(v);
  return v.trim() !== "" && Number.isFinite(n) ? n : null;
}

export function StrategyMatrix() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<StrategyMatrixT>(
    () => api.getStrategyMatrix(),
    [],
  );
  useAutoPoll(reload, "signals", { hasError: error != null });
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/settings"));

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
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
            ← Settings
          </button>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)" }}>
            <h1 className="screen-title" style={{ margin: 0 }}>Signal modules</h1>
            {data?.as_of && (
              <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>{timeAgo(data.as_of)}</span>
            )}
          </div>
          <p className="screen-sub" style={{ marginTop: "var(--s-1)" }}>
            Per-module weights and enabled state for the signal aggregator. Advisory
            only — tuning changes what the platform recommends, never places an order.
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
          <Button variant="neutral" onClick={() => resetGridLayout("strategy-matrix")}>Reset Layout</Button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, marginTop: "var(--s-4)" }}>
        {loading && !data && <Loading lines={4} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && data && (
          <DynamicGrid
            layoutKey="strategy-matrix"
            defaultLayouts={{
              lg: [
                { i: "meta-label", x: 0, y: 0, w: 12, h: 8, minW: 6, minH: 6 },
                { i: "context", x: 0, y: 8, w: 12, h: 4, minW: 6, minH: 3 },
                ...data.modules.map((m, i) => ({
                  i: m.name,
                  x: (i % 3) * 4,
                  y: 12 + Math.floor(i / 3) * 4,
                  w: 4,
                  h: 4,
                  minW: 3,
                  minH: 3,
                })),
              ],
            }}
          >
            <div key="meta-label">
              <div style={{ height: "100%" }}>
                <MetaLabelSection dist={data.meta_label} />
              </div>
            </div>
            
            {/* The rest is handled inside MatrixEditor but since it's a grid, we should probably pass the grid wrapper to MatrixEditor, or inline it. */}
            <MatrixEditor data={data} onReload={reload} />
          </DynamicGrid>
        )}
      </div>
    </div>
  );
}

/**
 * Portfolio-wide distribution of `meta_label_composite` — ports
 * `gui/panels/strategy_matrix.py::_render_meta_label_distribution`. Rides the
 * screen's single existing `GET /strategy/matrix` fetch, no second request.
 *
 * The `all_unity` info box is load-bearing, not decorative: with no
 * MetaLabelers registered in `ml.meta_labeling.global_meta_registry` (the
 * platform's current state), every module's `meta_label_proba` defaults to
 * 1.0 (a multiplicative no-op), so a single spike at 1.0 is the CORRECT
 * rendering — without this explanation an operator reads a one-bar chart as
 * broken.
 */
function MetaLabelSection({ dist }: { dist: MetaLabelDistribution }) {
  const chartData = useMemo(
    () =>
      dist.bins.map((b) => ({
        label: `${b.lo.toFixed(2)}–${b.hi.toFixed(2)}`,
        count: b.count,
      })),
    [dist.bins],
  );

  return (
    <section className="card card-pad" style={{ height: "100%" }} data-testid="meta-label-section">
      <div className="drag-handle" style={{ cursor: "grab", borderBottom: `1px solid ${theme.border}`, paddingBottom: "var(--s-1)", marginBottom: "var(--s-2)" }}><h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Meta-label confidence distribution</h2></div>
      <p style={{ margin: "0 0 var(--s-2-5)", fontSize: "var(--t-body)", color: theme.textMuted }}>
        Distribution of meta-label confidence (geometric mean of active
        modules' P(signal correct)) across all symbols in the last snapshot.
      </p>

      {dist.count === 0 ? (
        <div className="empty" data-testid="meta-label-empty" style={{ padding: "var(--s-5)" }}>
          {dist.reason ?? "No meta-label data available."}
        </div>
      ) : (
        <>
          <div style={{ height: 220 }} data-testid="meta-label-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                <CartesianGrid {...chartGridProps} />
                <XAxis
                  dataKey="label"
                  tick={{ ...chartAxisTick, fontSize: 9 }}
                  {...chartAxisLine}
                  interval={1}
                  angle={-45}
                  textAnchor="end"
                  height={50}
                />
                <YAxis tick={chartAxisTick} {...chartAxisLine} allowDecimals={false} />
                <ChartTooltip
                  contentStyle={chartTooltipStyle}
                  labelStyle={{ color: theme.textSecondary, fontSize: "var(--t-micro)" }}
                  itemStyle={{ fontSize: "var(--t-micro)" }}
                />
                <Bar dataKey="count" fill={theme.accent} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {dist.all_unity ? (
            <Notice variant="info" style={{ marginTop: "var(--s-2-5)" }} data-testid="meta-label-all-unity-notice">
              <span>ℹ️</span>
              <span>
                Every symbol shows exactly 1.0 — this is expected pre-Stage-4-deployment.
                No MetaLabelers are currently registered in{" "}
                <code>ml.meta_labeling.global_meta_registry</code>, so{" "}
                <code>meta_label_proba</code> defaults to 1.0 (a multiplicative
                no-op) for every signal module. This is NOT fabricated
                variation; the distribution will spread once real MetaLabelers
                are trained and registered.
              </span>
            </Notice>
          ) : (
            <p style={{ margin: "var(--s-2-5) 0 0", fontSize: "var(--t-label)", color: theme.textMuted }} data-testid="meta-label-gated-caption">
              {dist.count} symbols. {dist.n_gated} currently hard-gated to 0.0
              (a registered MetaLabeler's P(correct) fell below{" "}
              {dist.min_confidence.toFixed(2)}).
            </p>
          )}
        </>
      )}
    </section>
  );
}

function MatrixEditor({ data, onReload }: { data: StrategyMatrixT; onReload: () => void }) {
  const [edit, setEdit] = useState<EditState>(() => initEdit(data.modules, data.disabled));
  const [confirming, setConfirming] = useState(false);
  const mutation = useMutation(
    () =>
      api.setStrategyModules({
        weights: Object.fromEntries(
          Object.entries(edit.weights).map(([k, v]) => [k, Number(v)]),
        ),
        disabled: [...edit.disabled].sort(),
      }),
    { successMessage: "Strategy weights updated" },
  );
  const saved = mutation.result != null && mutation.error == null;

  const max = data.max_weight;
  const original = useMemo(() => initEdit(data.modules, data.disabled), [data]);

  const invalidNames = useMemo(() => {
    const bad = new Set<string>();
    for (const [name, v] of Object.entries(edit.weights)) {
      const n = parseWeight(v);
      if (n == null || n < 0 || n > max) bad.add(name);
    }
    return bad;
  }, [edit.weights, max]);

  const dirty = useMemo(() => {
    const wChanged = Object.keys(edit.weights).some(
      (k) => edit.weights[k] !== original.weights[k],
    );
    const dChanged =
      edit.disabled.size !== original.disabled.size ||
      [...edit.disabled].some((d) => !original.disabled.has(d));
    return wChanged || dChanged;
  }, [edit, original]);

  const changes = useMemo(() => {
    const weightDiffs: { name: string; from: string; to: string }[] = [];
    for (const k of Object.keys(edit.weights)) {
      if (edit.weights[k] !== original.weights[k]) {
        weightDiffs.push({ name: k, from: original.weights[k], to: edit.weights[k] });
      }
    }
    const toggles: { name: string; enabled: boolean }[] = [];
    const names = new Set([...edit.disabled, ...original.disabled]);
    for (const n of names) {
      const wasDisabled = original.disabled.has(n);
      const isDisabled = edit.disabled.has(n);
      if (wasDisabled !== isDisabled) toggles.push({ name: n, enabled: wasDisabled });
    }
    return { weightDiffs, toggles };
  }, [edit, original]);

  const setWeight = (name: string, v: string) =>
    setEdit((s) => ({ ...s, weights: { ...s.weights, [name]: v } }));

  const setEnabled = (name: string, enabled: boolean) =>
    setEdit((s) => {
      const d = new Set(s.disabled);
      if (enabled) d.delete(name);
      else d.add(name);
      return { ...s, disabled: d };
    });

  const canSave = data.writable && dirty && invalidNames.size === 0 && !mutation.pending;

  const doSave = async () => {
    await mutation.run();
    setConfirming(false);
    onReload(); // refresh so env_drift.detected surfaces; local edits are kept by the server echo
  };

  return (
    <>
      <div key="context">
        <section className="card card-pad drag-handle" style={{ display: "flex", flexDirection: "column", height: "100%", cursor: "grab" }}>
          <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-1)" }}>Context & Alerts</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", margin: "var(--s-1) 0 var(--s-3)" }}>
            <span className="chip">Regime {data.market_regime ?? "—"}</span>
            <span className="chip">Max weight {fmtNum(max, 0)}</span>
            {data.regime_overrides_active && <span className="chip">Regime overrides active</span>}
          </div>

          {!data.writable && (
            <Notice variant="warn" style={{ marginBottom: "var(--s-1)" }}>
              <span>{data.note}</span>
            </Notice>
          )}

          {data.env_drift.detected && (
            <Notice variant="info" style={{ marginBottom: "var(--s-1)" }} data-testid="env-drift-notice">
              <span>{data.env_drift.note}</span>
            </Notice>
          )}

          {saved && (
            <Notice variant="success" style={{ marginBottom: "var(--s-1)" }} data-testid="saved-notice">
              <span>
                Saved to .env. The running engine keeps the previous values until its
                next restart.
              </span>
            </Notice>
          )}
          
          {data.writable && (
            <div style={{ marginTop: "auto", paddingTop: "var(--s-2)" }}>
              <Button
                variant="primary"
                block
                disabled={!canSave}
                onClick={() => setConfirming(true)}
              >
                Save changes
              </Button>
            </div>
          )}
        </section>
      </div>

      {data.modules.map((m) => {
        const enabled = !edit.disabled.has(m.name);
        const invalid = invalidNames.has(m.name);
        return (
          <div key={m.name}>
            <section
              className="card card-pad"
              style={{ display: "flex", flexDirection: "column", height: "100%", opacity: enabled ? 1 : 0.6 }}
            >
              <div className="drag-handle" style={{ cursor: "grab", borderBottom: `1px solid ${theme.border}`, paddingBottom: "var(--s-2)", marginBottom: "var(--s-2)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontWeight: 700 }}>{m.name}</div>
                <Toggle
                  checked={enabled}
                  onChange={(v) => setEnabled(m.name, v)}
                  label={`${m.name} enabled`}
                  disabled={!data.writable}
                />
              </div>
              <div style={{ flex: 1, overflow: "auto" }}>
                <div style={{ fontSize: "var(--t-footnote)", color: theme.textMuted, marginTop: "var(--s-0-5)" }}>
                  {m.source === "snapshot"
                    ? "scored last run, no configured weight"
                    : m.source === "weights"
                      ? "configured, not scored last run"
                      : `${m.symbols_scored ?? "—"} symbols scored`}
                </div>
                <InfoTip
                  triggerStyle={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    background: "none",
                    border: "none",
                    padding: 0,
                    fontSize: "var(--t-micro)",
                    color: theme.textMuted,
                    marginTop: "var(--s-0-5)",
                    fontFamily: "monospace",
                    cursor: "pointer",
                  }}
                  content="sha256-prefix fingerprint of signals/<name>.py + its last-modified time"
                >
                  {m.version_hash
                    ? `v${m.version_hash} · modified ${timeAgo(m.last_modified)}`
                    : "no file on disk"}
                </InfoTip>
                <div style={{ marginTop: "var(--s-2-5)", maxWidth: "100%" }}>
                  <Input
                    label="Weight"
                    type="number"
                    min={0}
                    max={max}
                    step={1}
                    value={edit.weights[m.name] ?? ""}
                    onChange={(e) => setWeight(m.name, e.target.value)}
                    invalid={invalid}
                    hint={
                      m.pinned_zero
                        ? "Pinned to 0 — carries information via confidence, not score."
                        : invalid
                          ? `Must be a number in [0, ${max}].`
                          : undefined
                    }
                    disabled={!data.writable || m.pinned_zero}
                  />
                </div>
              </div>
            </section>
          </div>
        );
      })}

      {confirming && (
        <Modal ariaLabel="Confirm signal-module changes" onClose={() => setConfirming(false)}>
          <h2 style={{ fontSize: 18, margin: "0 0 var(--s-2)" }}>Confirm changes</h2>
          <p style={{ fontSize: "var(--t-body)", color: theme.textSecondary, marginTop: 0 }}>
            These write to <code>.env</code> and apply on the engine's next restart —
            not immediately.
          </p>
          {changes.weightDiffs.length > 0 && (
            <div style={{ marginTop: "var(--s-2)" }}>
              <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1)" }}>Weights</h3>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: "var(--t-body)", lineHeight: 1.6 }}>
                {changes.weightDiffs.map((d) => (
                  <li key={d.name}>
                    <strong>{d.name}</strong>: {d.from} → {d.to}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {changes.toggles.length > 0 && (
            <div style={{ marginTop: "var(--s-3)" }}>
              <h3 style={{ fontSize: "var(--t-body)", color: theme.textMuted, margin: "0 0 var(--s-1)" }}>Modules</h3>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: "var(--t-body)", lineHeight: 1.6 }}>
                {changes.toggles.map((t) => (
                  <li key={t.name}>
                    <strong>{t.name}</strong>: {t.enabled ? "enabled" : "disabled"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {mutation.error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
              <span>{mutation.error}</span>
            </Notice>
          )}
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button variant="neutral" block onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button variant="primary" block pending={mutation.pending} onClick={doSave}>
              Write to .env
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
