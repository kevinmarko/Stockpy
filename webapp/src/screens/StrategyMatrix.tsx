import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { StrategyMatrix as StrategyMatrixT, StrategyModuleRow } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, ErrorState, Input, Loading } from "../components/ui";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
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
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/settings"));

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
        ← Settings
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <h1 className="screen-title">Signal modules</h1>
        {data?.as_of && (
          <span style={{ fontSize: 12, color: theme.textMuted }}>{timeAgo(data.as_of)}</span>
        )}
      </div>
      <p className="screen-sub">
        Per-module weights and enabled state for the signal aggregator. Advisory
        only — tuning changes what the platform recommends, never places an order.
      </p>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && <MatrixEditor data={data} onReload={reload} />}
    </div>
  );
}

function MatrixEditor({ data, onReload }: { data: StrategyMatrixT; onReload: () => void }) {
  const [edit, setEdit] = useState<EditState>(() => initEdit(data.modules, data.disabled));
  const [confirming, setConfirming] = useState(false);
  const mutation = useMutation(() =>
    api.setStrategyModules({
      weights: Object.fromEntries(
        Object.entries(edit.weights).map(([k, v]) => [k, Number(v)]),
      ),
      disabled: [...edit.disabled].sort(),
    }),
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
      {/* Context row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "4px 0 12px" }}>
        <span className="chip">Regime {data.market_regime ?? "—"}</span>
        <span className="chip">Max weight {fmtNum(max, 0)}</span>
        {data.regime_overrides_active && <span className="chip">Regime overrides active</span>}
      </div>

      {!data.writable && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <span>{data.note}</span>
        </div>
      )}

      {data.env_drift.detected && (
        <div className="notice notice-info" style={{ marginBottom: 12 }} data-testid="env-drift-notice">
          <span>{data.env_drift.note}</span>
        </div>
      )}

      {saved && (
        <div className="notice notice-info" style={{ marginBottom: 12 }} data-testid="saved-notice">
          <span>
            Saved to .env. The running engine keeps the previous values until its
            next restart.
          </span>
        </div>
      )}

      {/* Module rows */}
      <div>
        {data.modules.map((m) => {
          const enabled = !edit.disabled.has(m.name);
          const invalid = invalidNames.has(m.name);
          return (
            <section
              key={m.name}
              className="card card-pad"
              style={{ marginBottom: 10, opacity: enabled ? 1 : 0.6 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontWeight: 700 }}>{m.name}</div>
                  <div style={{ fontSize: 11.5, color: theme.textMuted, marginTop: 2 }}>
                    {m.source === "snapshot"
                      ? "scored last run, no configured weight"
                      : m.source === "weights"
                        ? "configured, not scored last run"
                        : `${m.symbols_scored ?? "—"} symbols scored`}
                  </div>
                </div>
                <Toggle
                  checked={enabled}
                  onChange={(v) => setEnabled(m.name, v)}
                  label={`${m.name} enabled`}
                  disabled={!data.writable}
                />
              </div>
              <div style={{ marginTop: 10, maxWidth: 180 }}>
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
            </section>
          );
        })}
      </div>

      {data.writable && (
        <div style={{ position: "sticky", bottom: "var(--safe-bottom)", marginTop: 12 }}>
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

      {confirming && (
        <Modal ariaLabel="Confirm signal-module changes" onClose={() => setConfirming(false)}>
          <h2 style={{ fontSize: 18, margin: "0 0 8px" }}>Confirm changes</h2>
          <p style={{ fontSize: 13, color: theme.textSecondary, marginTop: 0 }}>
            These write to <code>.env</code> and apply on the engine's next restart —
            not immediately.
          </p>
          {changes.weightDiffs.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>Weights</h3>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
                {changes.weightDiffs.map((d) => (
                  <li key={d.name}>
                    <strong>{d.name}</strong>: {d.from} → {d.to}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {changes.toggles.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <h3 style={{ fontSize: 13, color: theme.textMuted, margin: "0 0 4px" }}>Modules</h3>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
                {changes.toggles.map((t) => (
                  <li key={t.name}>
                    <strong>{t.name}</strong>: {t.enabled ? "enabled" : "disabled"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {mutation.error && (
            <div className="notice notice-warn" style={{ marginTop: 12 }}>
              <span>{mutation.error}</span>
            </div>
          )}
          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
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
