import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { TunableField, TunablesResponse, TunablesUpdateResult } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Input, Loading, Notice, Select, Textarea } from "./ui";
import { Toggle } from "./Toggle";
import { TunableGroupCard } from "./TunableGroupCard";
import { theme } from "../theme";

/**
 * GenericSettingsEditor — THE settings-editor implementation for this PWA.
 *
 * Every scoped `.env`-write editor screen renders through this one component:
 * the general runtime tunables (`/settings/tunables`, SettingsManager.tsx) plus
 * the four narrower ones (`/settings/sentiment`, `/settings/sector-selection`,
 * `/settings/fmp`, `/settings/etf-transmission`), one per scoped GET/PUT pair
 * in `api/pilots_api.py` (`_TUNABLE_INDEX`, `_SENTIMENT_INDEX`,
 * `_SECTOR_SELECTION_INDEX`, `_FMP_INDEX`, `_ETF_TRANSMISSION_INDEX`). Each
 * screen is a thin wrapper supplying its own title/subtitle and its own api
 * method pair; the dirty-tracking, validation, save-only-changed-keys,
 * rejection-surfacing, and widget-selection logic all live here, once.
 *
 * This used to be two near-verbatim forks — SettingsManager.tsx carried its own
 * private copy of all of the above and had already drifted from this file in
 * small ways. Keep it one implementation: a per-field UI change made here must
 * not need making twice.
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

export interface GenericSettingsEditorProps {
  title: string;
  subtitle: ReactNode;
  backTo?: string;
  fetchSettings: () => Promise<TunablesResponse>;
  updateSettings: (
    values: Record<string, number | boolean | string>,
  ) => Promise<TunablesUpdateResult>;
  /**
   * Empty-state copy for when the backend exposes zero fields. Defaults to the
   * scoped-editor wording; the general tunables screen overrides it with its
   * own (and with an explicit note that nothing is fabricated when a value is
   * unavailable).
   */
  emptyTitle?: string;
  emptyHint?: string;
  /**
   * Optional screen-specific section rendered between the group cards and the
   * sticky Save bar, inside the same `hasFields` branch the fields render in.
   * Only `/settings/tunables` passes one today (its Danger Zone); the other
   * four editors pass nothing and render nothing extra, exactly as before.
   */
  dangerZone?: ReactNode;
}

export function GenericSettingsEditor({
  title,
  subtitle,
  backTo = "/settings",
  fetchSettings,
  updateSettings,
  emptyTitle = "No settings exposed",
  emptyHint = "The backend returned no editable settings for this section.",
  dangerZone,
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
        <EmptyState title={emptyTitle} hint={emptyHint} />
      )}
      {!loading && !error && data && hasFields && (
        <SettingsForm
          data={data}
          onReload={reload}
          updateSettings={updateSettings}
          dangerZone={dangerZone}
        />
      )}
    </div>
  );
}

function SettingsForm({
  data,
  onReload,
  updateSettings,
  dangerZone,
}: {
  data: TunablesResponse;
  onReload: () => void;
  updateSettings: (
    values: Record<string, number | boolean | string>,
  ) => Promise<TunablesUpdateResult>;
  dangerZone?: ReactNode;
}) {
  const flatFields = useMemo(() => data.groups.flatMap((g) => g.fields), [data]);
  const baselineInit = useMemo(() => buildBaseline(data.groups), [data]);
  const [baseline, setBaseline] = useState<Record<string, EditVal>>(baselineInit);
  const [edited, setEdited] = useState<Record<string, EditVal>>(baselineInit);

  const mutation = useMutation(updateSettings);

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
      onReload(); // refresh so env_drift.detected surfaces the pending write
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

      {dangerZone}

      <div style={{ position: "sticky", bottom: "var(--safe-bottom)", marginTop: "var(--s-3)" }}>
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

/**
 * Wire type "string" covers two very different widgets: a plain scalar
 * (SECTOR_FORECAST_CONFIG_PATH, PROMPT_REGISTRY_BACKEND, DEFAULT_TICKERS) and
 * a JSON blob (SECTOR_FORECAST_CONFIGS, CORS_ALLOWED_ORIGINS) — the backend
 * deliberately does NOT add a 5th TunableFieldType for the latter ("a JSON
 * blob is still a string on the wire"), so the frontend tells them apart by
 * content: a "string" field whose value/default parses as a JSON object or
 * array renders as a multi-line textarea instead of a single-line input.
 * Content-based (not key-name-based) so any future JSON-kind field the
 * backend adds picks up the right widget with zero frontend changes.
 */
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

/**
 * One editable field. This is the single place per-field UI is decided for all
 * five editors — the planned per-key liveness/safety metadata (an
 * applies-immediately vs. needs-restart badge, a disabled input for an
 * env-pinned value, a type-the-key confirmation for a `DANGEROUS_KEYS` field —
 * see `settings_keysets.py`) hooks in HERE, once, off new `TunableField`
 * fields, rather than being added per screen.
 */
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
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", margin: "var(--s-1-5) 0 0" }}>
            {f.description}
          </p>
        </>
      ) : f.type === "enum" ? (
        <Select
          label={f.key}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
          hint={f.description ?? undefined}
        />
      ) : isJsonBlob(f) ? (
        <Textarea
          label={f.key}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          spellCheck={false}
          monospace
          invalid={invalid}
          hint={f.description ?? undefined}
        />
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
          hint={invalid ? rangeMsg : f.description ?? undefined}
        />
      )}

      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-1-5) 0 0" }}>
        Default: {defaultLabel(f)}
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
