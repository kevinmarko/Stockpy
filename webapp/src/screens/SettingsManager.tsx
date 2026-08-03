import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { TunableField, TunablesResponse } from "../api/types";
import { clearAllCacheEntries } from "../api/offlineCache";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, EmptyState, ErrorState, Input, Loading, Notice, Select, Textarea } from "../components/ui";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
import { TunableGroupCard } from "../components/TunableGroupCard";
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
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
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

      <Notice variant="info" style={{ marginBottom: "var(--s-3)" }} data-testid="applies-notice">
        <span>ℹ️</span>
        <span>Changes apply on the next pipeline / daemon restart (no hot-reload).</span>
      </Notice>

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && !hasFields && (
        <EmptyState
          title="No tunables exposed"
          hint="The backend returned no editable settings. Nothing here is fabricated when a value is unavailable."
        />
      )}
      {!loading && !error && data && hasFields && (
        <TunablesEditor data={data} onReload={reload} />
      )}
    </div>
  );
}

function TunablesEditor({
  data,
  onReload,
}: {
  data: TunablesResponse;
  onReload: () => void;
}) {
  const flatFields = useMemo(
    () => data.groups.flatMap((g) => g.fields),
    [data],
  );
  const baselineInit = useMemo(() => buildBaseline(data.groups), [data]);
  const [baseline, setBaseline] = useState<Record<string, EditVal>>(baselineInit);
  const [edited, setEdited] = useState<Record<string, EditVal>>(baselineInit);

  // "Clear Data Cache" (Danger Zone) -- clears this browser's localStorage-backed
  // offline-response cache (webapp/src/api/offlineCache.ts). There is no
  // server-side cache-clearing endpoint for this button to call; this IS the
  // one cache the webapp itself owns and can honestly clear. See
  // clearAllCacheEntries()'s docstring for why a failure here is surfaced
  // rather than swallowed.
  const [cacheModalOpen, setCacheModalOpen] = useState(false);
  const [cacheResult, setCacheResult] = useState<{ ok: boolean; message: string } | null>(null);
  const confirmClearCache = () => {
    setCacheModalOpen(false);
    try {
      const n = clearAllCacheEntries();
      setCacheResult({
        ok: true,
        message: n > 0
          ? `Cleared ${n} cached response${n === 1 ? "" : "s"} from this browser.`
          : "Nothing to clear — no cached responses were stored in this browser.",
      });
    } catch (err: any) {
      setCacheResult({ ok: false, message: `Failed to clear cache: ${err?.message || err}` });
    }
  };

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
      onReload(); // refresh so env_drift.detected surfaces the pending write
    }
  };

  return (
    <>
      {data.env_drift.detected && (
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

      <section className="card card-pad" style={{ marginBottom: "var(--s-3)", border: `1px solid ${theme.decline}`, background: "rgba(220, 38, 38, 0.05)" }}>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)", color: theme.decline }}>Danger Zone</h2>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginBottom: "var(--s-3)", marginTop: 0 }}>
          Irreversible and destructive actions. Please be certain before proceeding.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
            <div>
              <div style={{ fontWeight: 700, color: theme.textPrimary }}>Restart Daemon</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>Force restart the background engine process.</div>
            </div>
            <Button
              variant="neutral"
              onClick={async () => {
                if (!confirm("Are you sure you want to restart the daemon? This will interrupt any running jobs.")) return;
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
          </div>
          <div style={{ height: 1, background: theme.borderStrong, margin: "var(--s-1) 0" }} />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
            <div>
              <div style={{ fontWeight: 700, color: theme.textPrimary }}>Clear Data Cache</div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
                Clear this browser&apos;s cached API responses (used as an offline fallback). Does
                not touch server-side data or the running engine.
              </div>
            </div>
            <button
              style={{
                padding: "8px 16px",
                borderRadius: "var(--r-sm)",
                background: "transparent",
                color: theme.decline,
                fontWeight: 600,
                border: `1px solid ${theme.decline}`,
                cursor: "pointer",
                fontSize: "var(--t-caption)",
              }}
              onClick={() => setCacheModalOpen(true)}
              data-testid="clear-cache-button"
            >
              Clear Cache
            </button>
          </div>

          {cacheResult && (
            <Notice variant={cacheResult.ok ? "success" : "warn"} data-testid="cache-cleared-notice">
              <span>{cacheResult.ok ? "✅" : "⚠️"}</span>
              <span>{cacheResult.message}</span>
            </Notice>
          )}
        </div>
      </section>

      {cacheModalOpen && (
        <Modal ariaLabel="Confirm clear data cache" onClose={() => setCacheModalOpen(false)}>
          <div data-testid="clear-cache-confirm">
            <div className="tile-label" style={{ marginBottom: "var(--s-2)" }}>
              Clear data cache?
            </div>
            <p style={{ color: theme.textSecondary, marginTop: 0 }}>
              This clears this browser&apos;s cached API responses (used as an offline fallback
              when the network is unreachable). It does not affect server-side data, the
              database, or the running engine, and cannot be undone.
            </p>
            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <Button variant="neutral" onClick={() => setCacheModalOpen(false)} data-testid="clear-cache-cancel">
                Cancel
              </Button>
              <Button variant="primary" onClick={confirmClearCache} data-testid="clear-cache-confirm-yes">
                Yes, clear it
              </Button>
            </div>
          </div>
        </Modal>
      )}

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
