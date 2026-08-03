import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { TunableField, TunablesResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Input, Loading, Notice, Select, Textarea } from "../components/ui";
import { Toggle } from "../components/Toggle";
import { TunableGroupCard } from "../components/TunableGroupCard";
import { theme } from "../theme";
import { TagInput } from "./TagInput";

type EditVal = string | boolean;

function encodeValue(f: TunableField): EditVal {
  if (f.type === "boolean") return f.value === true;
  return f.value === null || f.value === undefined ? "" : String(f.value);
}

function buildBaseline(groups: TunablesResponse["groups"]): Record<string, EditVal> {
  const out: Record<string, EditVal> = {};
  for (const g of groups) for (const f of g.fields) out[f.key] = encodeValue(f);
  return out;
}

interface GenericSettingsEditorProps {
  title: string;
  subtitle: string;
  backTo?: string;
  fetchSettings: () => Promise<TunablesResponse>;
  updateSettings: (values: Record<string, number | boolean | string>) => Promise<{
    written: Record<string, any>;
    rejected: Record<string, string>;
    applies: string;
    note?: string;
  }>;
}

export function GenericSettingsEditor({
  title,
  subtitle,
  backTo = "/settings",
  fetchSettings,
  updateSettings,
}: GenericSettingsEditorProps) {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<TunablesResponse>(fetchSettings, []);
  const back = () => (window.history.length > 1 ? nav(-1) : nav(backTo));

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
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
        }}
      >
        ← Settings
      </button>
      <h1 className="screen-title">{title}</h1>
      <p className="screen-sub">{subtitle}</p>

      <Notice variant="info" style={{ marginBottom: "var(--s-3)" }} data-testid="applies-notice">
        <span>ℹ️</span>
        <span>Changes apply on the next pipeline / daemon restart (no hot-reload).</span>
      </Notice>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && !hasFields && (
        <EmptyState
          title="No settings exposed"
          hint="The backend returned no editable settings for this section."
        />
      )}
      {!loading && !error && data && hasFields && (
        <SettingsForm data={data} onReload={reload} updateSettings={updateSettings} />
      )}
    </div>
  );
}

function SettingsForm({
  data,
  onReload,
  updateSettings,
}: {
  data: TunablesResponse;
  onReload: () => void;
  updateSettings: (values: Record<string, number | boolean | string>) => Promise<{
    written: Record<string, any>;
    rejected: Record<string, string>;
    applies: string;
    note?: string;
  }>;
}) {
  const flatFields = useMemo(() => data.groups.flatMap((g) => g.fields), [data]);
  const baselineInit = useMemo(() => buildBaseline(data.groups), [data]);
  const [baseline, setBaseline] = useState<Record<string, EditVal>>(baselineInit);
  const [edited, setEdited] = useState<Record<string, EditVal>>(baselineInit);

  const mutation = useMutation(updateSettings);

  const setVal = (key: string, v: EditVal) =>
    setEdited((s) => ({ ...s, [key]: v }));

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
    [flatFields, edited, baseline]
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
      setBaseline((b) => {
        const next = { ...b };
        for (const [k, v] of Object.entries(res.written)) {
          next[k] = typeof v === "boolean" ? v : String(v);
        }
        return next;
      });
      onReload();
    }
  };

  return (
    <>
      {data.env_drift?.detected && (
        <Notice variant="info" style={{ marginBottom: "var(--s-3)" }} data-testid="env-drift-notice">
          <span>ℹ️</span>
          <span>
            {data.env_drift.keys.length} setting{data.env_drift.keys.length === 1 ? "" : "s"}{" "}
            differ{data.env_drift.keys.length === 1 ? "s" : ""} from the running process
            ({data.env_drift.keys.join(", ")}). {data.env_drift.note}
          </span>
          <Button
            variant="neutral"
            style={{ marginLeft: 12, fontSize: 12.5, padding: "4px 10px" }}
            onClick={async () => {
              try {
                const res = await api.restartDaemon();
                alert(res.message);
              } catch (err: any) {
                alert(`Failed to request restart: ${err.message || err}`);
              }
            }}
          >
            Restart Daemon
          </Button>
        </Notice>
      )}

      {writtenKeys.length > 0 && (
        <Notice variant="success" style={{ marginBottom: "var(--s-3)" }} data-testid="written-notice">
          <span>✅</span>
          <span>
            Saved to .env: {writtenKeys.join(", ")}. The running engine keeps the
            previous values until its next restart.
          </span>
        </Notice>
      )}

      {Object.keys(rejected).length > 0 && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }} data-testid="rejected-notice">
          <span>⚠️</span>
          <span>
            {Object.keys(rejected).length} change
            {Object.keys(rejected).length === 1 ? "" : "s"} rejected — see the
            highlighted fields below.
          </span>
        </Notice>
      )}

      {mutation.error && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }}>
          <span>⚠️</span>
          <span>{mutation.error}</span>
        </Notice>
      )}

      {data.groups.map((group, idx) => {
        if (group.fields.length === 0) return null;
        const groupDirtyCount = group.fields.filter((f) => edited[f.key] !== baseline[f.key]).length;
        const groupRejectedCount = group.fields.filter((f) => Boolean(rejected[f.key])).length;
        return (
          <TunableGroupCard
            key={group.name}
            name={group.name}
            fields={group.fields}
            defaultOpen={data.groups.length <= 3 || idx === 0}
            dirtyCount={groupDirtyCount}
            rejectedCount={groupRejectedCount}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
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
          </TunableGroupCard>
        );
      })}

      {dirty && (
        <div
          style={{
            position: "sticky",
            bottom: "var(--safe-bottom)",
            marginTop: "var(--s-4)",
            padding: "var(--s-2)",
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: "var(--r-md)",
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            zIndex: 10,
          }}
        >
          <Button variant="primary" block disabled={!canSave} pending={mutation.pending} onClick={doSave}>
            Save {dirtyKeys.length} change{dirtyKeys.length === 1 ? "" : "s"}
          </Button>
        </div>
      )}
    </>
  );
}

function isJsonBlob(f: TunableField): boolean {
  if (f.type !== "string") return false;
  const probe = f.value ?? f.default;
  if (typeof probe !== "string" || probe === "") return false;
  try {
    const parsed = JSON.parse(probe);
    return parsed !== null && typeof parsed === "object";
  } catch {
    return false;
  }
}

function humanizeKey(key: string): string {
  if (!key.includes("_") && key.includes(" ")) return key;
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function isTagList(f: TunableField): boolean {
  if (f.type !== "string") return false;
  return f.key.endsWith("_SOURCES") || f.key.endsWith("_TICKERS") || f.key.endsWith("_LIST");
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
            label={humanizeKey(f.key)}
          />
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", margin: "var(--s-1-5) 0 0" }}>
            {f.description}
          </p>
        </>
      ) : f.type === "enum" ? (
        <Select
          label={humanizeKey(f.key)}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
          hint={f.description ?? undefined}
        />
      ) : isJsonBlob(f) ? (
        <Textarea
          label={humanizeKey(f.key)}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          spellCheck={false}
          monospace
          invalid={invalid}
          hint={f.description ?? undefined}
        />
      ) : isTagList(f) ? (
        <TagInput
          label={humanizeKey(f.key)}
          value={typeof value === "string" && value.length > 0 ? value.split(",").map(s => s.trim()) : []}
          onChange={(arr) => onChange(arr.join(","))}
          hint={f.description ?? undefined}
          invalid={invalid}
        />
      ) : (
        <Input
          label={humanizeKey(f.key)}
          type={f.type === "number" ? "number" : "text"}
          inputMode={f.type === "number" ? "decimal" : undefined}
          min={f.min}
          max={f.max}
          step={f.step}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          invalid={invalid}
          hint={invalid ? rangeMsg : f.description ?? undefined}
        />
      )}

      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-1-5) 0 0" }}>
        Default: {f.default === null || f.default === undefined ? "—" : String(f.default)}
      </p>

      {rejectedReason && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2)" }} data-testid={`rejected-${f.key}`}>
          <span>⚠️</span>
          <span>{rejectedReason}</span>
        </Notice>
      )}
    </div>
  );
}
