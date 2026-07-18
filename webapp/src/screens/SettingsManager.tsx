import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { TunableField, TunablesResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Input, Loading } from "../components/ui";
import { Toggle } from "../components/Toggle";
import { theme } from "../theme";

/**
 * Settings Manager — read + edit the platform's general runtime tunables
 * (GET/PUT /settings/tunables). A `.env`-write surface, so it lives under
 * /settings, reached from the "Runtime tunables" card, mirroring how Strategy
 * Matrix and the AI Control Center each got their own /settings sub-route once
 * they grew a write path.
 *
 * Honesty: an `.env` write does NOT reach the running process (settings is a
 * process-lifetime singleton) — hence the persistent "applies on next restart"
 * notice and the `applies: "next_daemon_restart"` contract. After Save the
 * screen surfaces exactly which keys the server `written`, surfaces every
 * per-key `rejected` reason (never swallowed), and resets the dirty baseline
 * for written keys only. A field whose `value` is null renders an empty input,
 * never a fabricated 0 (CONSTRAINT #4).
 */

// String-backed for number/enum/string inputs; boolean for toggles.
type EditVal = string | boolean;

function encodeValue(f: TunableField): EditVal {
  if (f.type === "boolean") return f.value === true;
  // number/enum/string: null -> "" (empty input), never a fabricated default.
  return f.value === null || f.value === undefined ? "" : String(f.value);
}

function buildBaseline(groups: TunablesResponse["groups"]): Record<string, EditVal> {
  const out: Record<string, EditVal> = {};
  for (const g of groups) for (const f of g.fields) out[f.key] = encodeValue(f);
  return out;
}

export function SettingsManager() {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<TunablesResponse>(
    () => api.getTunables(),
    [],
  );
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/settings"));

  const hasFields = Boolean(data?.groups.some((g) => g.fields.length > 0));

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
      <h1 className="screen-title">Runtime tunables</h1>
      <p className="screen-sub">
        General platform settings (sizing, forecasting, data). Advisory only —
        tuning changes what the platform computes and recommends, never places
        an order.
      </p>

      <div className="notice notice-info" style={{ marginBottom: 12 }} data-testid="applies-notice">
        <span>ℹ️</span>
        <span>Changes apply on the next pipeline / daemon restart (no hot-reload).</span>
      </div>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && !hasFields && (
        <EmptyState
          title="No tunables exposed"
          hint="The backend returned no editable settings. Nothing here is fabricated when a value is unavailable."
        />
      )}
      {!loading && !error && data && hasFields && <TunablesEditor data={data} />}
    </div>
  );
}

function TunablesEditor({ data }: { data: TunablesResponse }) {
  const flatFields = useMemo(
    () => data.groups.flatMap((g) => g.fields),
    [data],
  );
  const baselineInit = useMemo(() => buildBaseline(data.groups), [data]);
  const [baseline, setBaseline] = useState<Record<string, EditVal>>(baselineInit);
  const [edited, setEdited] = useState<Record<string, EditVal>>(baselineInit);

  const mutation = useMutation((values: Record<string, number | boolean | string>) =>
    api.updateTunables(values),
  );

  const setVal = (key: string, v: EditVal) =>
    setEdited((s) => ({ ...s, [key]: v }));

  // A number field is invalid only when it's dirty AND not a finite in-bounds
  // number. An unchanged field (including one that started null/empty) is never
  // flagged, so a partially-set config doesn't block an unrelated edit's Save.
  const invalidKeys = useMemo(() => {
    const bad = new Set<string>();
    for (const f of flatFields) {
      if (f.type !== "number") continue;
      if (edited[f.key] === baseline[f.key]) continue;
      const s = String(edited[f.key]);
      if (s.trim() === "") {
        bad.add(f.key);
        continue;
      }
      const n = Number(s);
      if (!Number.isFinite(n)) {
        bad.add(f.key);
        continue;
      }
      if ((f.min !== undefined && n < f.min) || (f.max !== undefined && n > f.max)) {
        bad.add(f.key);
      }
    }
    return bad;
  }, [flatFields, edited, baseline]);

  const dirtyKeys = useMemo(
    () => flatFields.filter((f) => edited[f.key] !== baseline[f.key]).map((f) => f.key),
    [flatFields, edited, baseline],
  );
  const dirty = dirtyKeys.length > 0;
  const canSave = dirty && invalidKeys.size === 0 && !mutation.pending;

  const rejected = mutation.result?.rejected ?? {};
  const writtenKeys = mutation.result ? Object.keys(mutation.result.written) : [];

  const doSave = async () => {
    const payload: Record<string, number | boolean | string> = {};
    for (const f of flatFields) {
      if (edited[f.key] === baseline[f.key]) continue;
      const cur = edited[f.key];
      if (f.type === "boolean") payload[f.key] = cur as boolean;
      else if (f.type === "number") payload[f.key] = Number(cur);
      else payload[f.key] = String(cur);
    }
    const res = await mutation.run(payload);
    if (res) {
      // Reset the dirty baseline for accepted keys only; rejected keys stay
      // dirty so the operator can fix and re-submit them.
      setBaseline((b) => {
        const next = { ...b };
        for (const [k, v] of Object.entries(res.written)) {
          next[k] = typeof v === "boolean" ? v : String(v);
        }
        return next;
      });
    }
  };

  return (
    <>
      {writtenKeys.length > 0 && (
        <div className="notice notice-info" style={{ marginBottom: 12 }} data-testid="written-notice">
          <span>✅</span>
          <span>
            Saved to .env: {writtenKeys.join(", ")}. The running engine keeps the
            previous values until its next restart.
          </span>
        </div>
      )}

      {Object.keys(rejected).length > 0 && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }} data-testid="rejected-notice">
          <span>⚠️</span>
          <span>
            {Object.keys(rejected).length} change
            {Object.keys(rejected).length === 1 ? "" : "s"} rejected — see the
            highlighted fields below.
          </span>
        </div>
      )}

      {mutation.error && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <span>⚠️</span>
          <span>{mutation.error}</span>
        </div>
      )}

      {data.groups.map((group) =>
        group.fields.length === 0 ? null : (
          <section key={group.name} className="card card-pad" style={{ marginBottom: 12 }}>
            <h2 style={{ margin: "0 0 10px", fontSize: "var(--t-title)" }}>{group.name}</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {group.fields.map((f) => (
                <FieldRow
                  key={f.key}
                  field={f}
                  value={edited[f.key]}
                  onChange={(v) => setVal(f.key, v)}
                  invalid={invalidKeys.has(f.key)}
                  rejectedReason={rejected[f.key] ?? null}
                />
              ))}
            </div>
          </section>
        ),
      )}

      <div style={{ position: "sticky", bottom: "var(--safe-bottom)", marginTop: 12 }}>
        <Button variant="primary" block disabled={!canSave} pending={mutation.pending} onClick={doSave}>
          {dirty ? `Save ${dirtyKeys.length} change${dirtyKeys.length === 1 ? "" : "s"}` : "Save changes"}
        </Button>
      </div>
    </>
  );
}

function defaultLabel(f: TunableField): string {
  if (f.default === null || f.default === undefined) return "—";
  return String(f.default);
}

function FieldRow({
  field: f,
  value,
  onChange,
  invalid,
  rejectedReason,
}: {
  field: TunableField;
  value: EditVal;
  onChange: (v: EditVal) => void;
  invalid: boolean;
  rejectedReason: string | null;
}) {
  const rangeMsg =
    f.min !== undefined || f.max !== undefined
      ? `Must be a number in [${f.min ?? "−∞"}, ${f.max ?? "∞"}].`
      : "Must be a number.";

  return (
    <div>
      {f.type === "boolean" ? (
        <>
          <Toggle
            checked={value === true}
            onChange={(v) => onChange(v)}
            label={f.key}
          />
          <p style={{ color: theme.textSecondary, fontSize: 12.5, margin: "6px 0 0" }}>
            {f.description}
          </p>
        </>
      ) : f.type === "enum" ? (
        <label style={{ display: "block" }}>
          <span
            className="tile-label"
            style={{ display: "block", marginBottom: 6 }}
          >
            {f.key}
          </span>
          <select
            value={String(value)}
            aria-label={f.key}
            onChange={(e) => onChange(e.target.value)}
            style={{
              fontSize: "var(--t-input)",
              padding: "10px 12px",
              width: "100%",
              borderRadius: "var(--r-md)",
              background: theme.surface2,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
            }}
          >
            {(f.options ?? []).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <p style={{ color: theme.textSecondary, fontSize: 12.5, margin: "6px 0 0" }}>
            {f.description}
          </p>
        </label>
      ) : (
        <Input
          label={f.key}
          type={f.type === "number" ? "number" : "text"}
          inputMode={f.type === "number" ? "decimal" : undefined}
          min={f.min}
          max={f.max}
          step={f.step}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          invalid={invalid}
          hint={invalid ? rangeMsg : f.description}
        />
      )}

      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "6px 0 0" }}>
        Default: {defaultLabel(f)}
      </p>

      {rejectedReason && (
        <div className="notice notice-warn" style={{ marginTop: 8 }} data-testid={`rejected-${f.key}`}>
          <span>⚠️</span>
          <span>{rejectedReason}</span>
        </div>
      )}
    </div>
  );
}
