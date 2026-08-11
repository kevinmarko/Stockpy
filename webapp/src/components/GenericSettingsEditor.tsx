import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import toast from "react-hot-toast";
import { api } from "../api/client";
import type {
  SettingsConfirmMap,
  TunableField,
  TunablesResponse,
  TunablesUpdateResult,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import {
  Button,
  EmptyState,
  ErrorState,
  InfoTip,
  Input,
  Loading,
  Notice,
  Select,
  Textarea,
} from "./ui";
import { Toggle } from "./Toggle";
import { TunableGroupCard } from "./TunableGroupCard";
import { Modal } from "./Modal";
import { theme } from "../theme";
import { TagInput } from "./TagInput";
import { DynamicGrid } from "./DynamicGrid";
import {
  buildConfirmMap,
  dangerousKeysIn,
  fieldBadge,
  isFieldEditable,
  resolveLiveness,
  saveOutcomeMessage,
  screenAppliesNotice,
} from "../settingsLiveness";

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
 * Honesty: whether a write reaches the RUNNING process is now decided PER
 * FIELD, from the backend's `liveness` metadata, not asserted screen-wide. This
 * component used to show one blanket "changes apply on the next pipeline /
 * daemon restart (no hot-reload)" notice on every screen; that was true when a
 * write could only land in `.env`, and became a false blanket claim once the
 * backend could apply some fields live. It is now replaced by a per-field badge
 * plus a summary notice derived from what this screen's fields actually report.
 *
 * Three further liveness-driven behaviours, all decided here once for all five
 * editors rather than per screen:
 *   - an `env_pinned` field's input is genuinely disabled (a shell export wins,
 *     so letting it be edited would invite a save that silently cannot work);
 *   - a `dangerous` field (`settings_keysets.DANGEROUS_KEYS`) requires the
 *     operator to type its name before Save will submit it — the UI half of a
 *     gate that is ALSO enforced server-side, which is what actually makes it
 *     safe;
 *   - post-save feedback distinguishes "applied now" from "saved, needs
 *     restart" instead of asserting the latter for everything.
 *
 * After Save the screen surfaces exactly which keys the server `written`,
 * surfaces every per-key `rejected` reason (never swallowed), and resets the
 * dirty baseline for written keys only. A field whose `value` is null renders
 * an empty input, never a fabricated 0 (CONSTRAINT #4).
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

/**
 * Grid-row height for a settings group's card, scaled to its actual field
 * count instead of a flat constant. Every group used to get the same `h: 4`
 * (4 grid rows) regardless of how many fields it held — the underlying grid
 * library does not auto-grow an item to fit its content, so a group with more
 * than a couple of fields was a real content-clipping risk. `TunableGroupCard`'s
 * own body already scrolls internally (`overflow: "auto"`) as a floor, but a
 * two-line internal scrollbar for a dozen fields is still bad UX — sizing the
 * card itself to roughly fit its fields is the actual fix. Base rows cover the
 * header + card padding; two rows per field is a rough fit for a labeled
 * input/toggle plus its hint/description text at `DynamicGrid`'s default
 * `rowHeight` (30px). Capped so one outsized group can't push every other card
 * off-screen — the internal scroll floor still catches the rest.
 */
const GROUP_BASE_ROWS = 2;
const GROUP_ROWS_PER_FIELD = 2;
const GROUP_MAX_ROWS = 16;

function computeGroupHeight(fieldCount: number): number {
  return Math.min(GROUP_BASE_ROWS + fieldCount * GROUP_ROWS_PER_FIELD, GROUP_MAX_ROWS);
}

export interface GenericSettingsEditorProps {
  title: string;
  subtitle: ReactNode;
  /**
   * Stable, unique, kebab-case identifier for this screen's saved grid
   * layout (`grid-layout-settings-<settingsKey>` in localStorage). Deliberately
   * separate from `title` — the human-readable display title is expected to
   * change over time (copy edits, rebranding), and deriving the persistence
   * key from it meant a title rename silently orphaned every operator's saved
   * layout for that screen. Callers pass their route segment under
   * `/settings/*` (e.g. "sentiment", "etf-transmission", "tunables") so the
   * key tracks the screen's identity, not its copy.
   */
  settingsKey: string;
  backTo?: string;
  fetchSettings: () => Promise<TunablesResponse>;
  updateSettings: (
    values: Record<string, number | boolean | string>,
    confirm?: SettingsConfirmMap,
  ) => Promise<TunablesUpdateResult>;
  /** Optional key -> human label override; falls back to `humanizeKey(key)`. */
  labelMap?: Record<string, string>;
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
  settingsKey,
  backTo = "/settings",
  fetchSettings,
  updateSettings,
  labelMap,
  emptyTitle = "No settings exposed",
  emptyHint = "The backend returned no editable settings for this section.",
  dangerZone,
}: GenericSettingsEditorProps) {
  const nav = useNavigate();
  const { data, loading, error, status, reload } = useApi<TunablesResponse>(fetchSettings, []);
  const back = () => (window.history.length > 1 ? nav(-1) : nav(backTo));

  // The last write's result is held HERE, in the parent, rather than inside
  // SettingsForm's own `useMutation`. A successful save calls `reload()`, and
  // `useApi.reload()` sets `loading` — which unmounts SettingsForm and would
  // take the mutation result down with it, making the post-save message (and
  // every per-key rejection reason) flash and vanish before it could be read.
  // This component does not unmount on reload, so the feedback survives it.
  const [lastResult, setLastResult] = useState<TunablesUpdateResult | null>(null);
  const outcome = lastResult ? saveOutcomeMessage(lastResult) : null;
  const rejectedCount = Object.keys(lastResult?.rejected ?? {}).length;

  const hasFields = Boolean(data?.groups.some((g) => g.fields.length > 0));

  // Replaces the old unconditional restart notice. Derived from what THIS
  // screen's fields actually report, and `null` (rendered as nothing) when
  // every field applies immediately — where a restart notice would be wrong.
  // While loading there is no data to summarise, so nothing is claimed.
  const appliesNotice = data ? screenAppliesNotice(data.applies, data.applies_counts) : null;

  return (
    <div className="screen" style={{ display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <button
            onClick={back}
            style={{ background: "none", padding: 0, cursor: "pointer", color: "var(--text-secondary)", fontSize: "var(--t-callout)", marginBottom: "var(--s-2)", border: "none" }}
          >
            ← Settings
          </button>
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--s-2)" }}>
            <h1 className="screen-title" style={{ margin: 0 }}>{title}</h1>
          </div>
          <p className="screen-sub" style={{ marginTop: "var(--s-1)" }}>{subtitle}</p>
        </div>
      </div>

      {appliesNotice && (
        <Notice
          variant={appliesNotice.variant}
          style={{ marginBottom: "var(--s-3)" }}
          data-testid="applies-notice"
        >
          <span>{appliesNotice.variant === "warn" ? "⚠️" : "ℹ️"}</span>
          <span>{appliesNotice.text}</span>
        </Notice>
      )}

      {/*
        The post-save notices render HERE, in the parent, not inside
        SettingsForm — SettingsForm unmounts while `reload()` is in flight, so
        anything rendered there would disappear at exactly the moment the
        operator wants to read it. The per-FIELD rejection reasons stay with
        their fields (they have nowhere else to be) and come back with them,
        because `lastResult` outlives the remount.
      */}
      {outcome && (
        <Notice
          variant={outcome.variant === "success" ? "success" : "info"}
          style={{ marginBottom: "var(--s-3)" }}
          data-testid="written-notice"
        >
          <span>{outcome.variant === "success" ? "✅" : "💾"}</span>
          <span>{outcome.text}</span>
        </Notice>
      )}

      {rejectedCount > 0 && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }} data-testid="rejected-notice">
          <span>⚠️</span>
          <span>
            {rejectedCount} change{rejectedCount === 1 ? "" : "s"} rejected — see the
            highlighted fields below.
          </span>
        </Notice>
      )}

      {loading && <Loading lines={4} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && !hasFields && (
        <EmptyState title={emptyTitle} hint={emptyHint} />
      )}
      {!loading && !error && data && hasFields && (
        <SettingsForm
          settingsKey={settingsKey}
          data={data}
          onReload={reload}
          updateSettings={updateSettings}
          labelMap={labelMap}
          dangerZone={dangerZone}
          lastResult={lastResult}
          onResult={setLastResult}
        />
      )}
    </div>
  );
}

function SettingsForm({
  settingsKey,
  data,
  onReload,
  updateSettings,
  labelMap,
  dangerZone,
  lastResult,
  onResult,
}: {
  settingsKey: string;
  data: TunablesResponse;
  onReload: () => void;
  updateSettings: (
    values: Record<string, number | boolean | string>,
    confirm?: SettingsConfirmMap,
  ) => Promise<TunablesUpdateResult>;
  labelMap?: Record<string, string>;
  dangerZone?: ReactNode;
  /** The last write's result, owned by the parent so it survives a reload. */
  lastResult: TunablesUpdateResult | null;
  onResult: (r: TunablesUpdateResult) => void;
}) {
  const flatFields = useMemo(() => data.groups.flatMap((g) => g.fields), [data]);
  const baselineInit = useMemo(() => buildBaseline(data.groups), [data]);
  const [baseline, setBaseline] = useState<Record<string, EditVal>>(baselineInit);
  const [edited, setEdited] = useState<Record<string, EditVal>>(baselineInit);

  const mutation = useMutation(updateSettings);

  // Toast feedback for a failed save. `mutation.error` is React state set
  // inside `mutation.run`'s catch block -- reading it synchronously right
  // after `await mutation.run(...)` in `submit` below would see this render's
  // STALE closed-over value, not the freshly-set one (the state update lands
  // on the next render). An effect reacting to the state itself is what
  // actually observes the fresh error, once per genuine failure ( `run` resets
  // `error` to null at the start of every attempt, so this never double-fires
  // for the same failure). Follows this codebase's house style for a failed-
  // mutation toast (see Toggle.tsx's `handleChange` catch block): a bold title
  // line plus the real error message, never a bare/generic string.
  useEffect(() => {
    if (!mutation.error) return;
    toast.error(
      <div style={{ display: "flex", flexDirection: "column" }}>
        <span style={{ fontWeight: 600, fontSize: "var(--t-callout)" }}>Save failed</span>
        <span
          style={{ color: "var(--text-secondary)", fontSize: "var(--t-caption)", marginTop: "4px" }}
        >
          {mutation.error}
        </span>
      </div>,
    );
  }, [mutation.error]);

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

  // An env-pinned field is not editable, so it can never become dirty and must
  // never be submitted. Filtering here (not only in the input's `disabled`)
  // keeps a stale edited-value from a previous render out of the payload.
  const dirtyKeys = useMemo(
    () =>
      flatFields
        .filter((f) => isFieldEditable(f) && edited[f.key] !== baseline[f.key])
        .map((f) => f.key),
    [flatFields, edited, baseline],
  );
  const dirty = dirtyKeys.length > 0;
  const canSave = dirty && invalidKeys.size === 0 && !mutation.pending;

  const rejected = lastResult?.rejected ?? {};

  // The dangerous subset of what is about to be written. Non-empty means Save
  // must route through the confirmation dialog first.
  const pendingDangerous = useMemo(
    () => dangerousKeysIn(flatFields, dirtyKeys),
    [flatFields, dirtyKeys],
  );
  const [confirmOpen, setConfirmOpen] = useState(false);

  // Two-column masonry-style packing: each group's height is sized to its own
  // field count (see `computeGroupHeight`) rather than a flat constant, so the
  // running per-column `y` offset has to be tracked explicitly instead of the
  // old `Math.floor(idx / 2) * 4` (which only worked because every item was
  // the same fixed height). `DynamicGrid`'s vertical compactor still resolves
  // any remaining slack once real content mounts; this is only the sane
  // starting point used the first time a screen renders (before an operator's
  // own dragged/resized layout takes over from localStorage).
  const groupLayouts = useMemo(() => {
    const colHeights = [0, 0];
    const groups = data.groups.map((g, idx) => {
      const col = idx % 2;
      const h = computeGroupHeight(g.fields.length);
      const y = colHeights[col];
      colHeights[col] += h;
      return { i: `group-${idx}`, x: col * 6, y, w: 6, h };
    });
    const danger = dangerZone
      ? [{ i: "danger-zone", x: 0, y: Math.max(colHeights[0], colHeights[1]), w: 12, h: 4 }]
      : [];
    return [...groups, ...danger];
  }, [data.groups, dangerZone]);

  const buildPayload = () => {
    const payload: Record<string, number | boolean | string> = {};
    for (const f of flatFields) {
      if (!dirtyKeys.includes(f.key)) continue;
      const cur = edited[f.key];
      if (f.type === "boolean") payload[f.key] = cur as boolean;
      else if (f.type === "number") payload[f.key] = Number(cur);
      else payload[f.key] = String(cur);
    }
    return payload;
  };

  const submit = async (confirm: SettingsConfirmMap) => {
    const res = await mutation.run(buildPayload(), confirm);
    if (res) {
      onResult(res);
      // `res` is the mutation's own fresh return value (not React state), so
      // it's safe to read directly here -- unlike `mutation.error` above.
      // Reuse the actual written-key count rather than a generic "Saved" so
      // a partially-rejected batch doesn't overclaim.
      const writtenCount = Object.keys(res.written).length;
      if (writtenCount > 0) {
        toast.success(`Saved ${writtenCount} setting${writtenCount === 1 ? "" : "s"}`);
      }
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

  const doSave = async () => {
    // A batch touching a DANGEROUS_KEYS field never goes straight through: the
    // dialog is what produces the `confirm` map. Sending no confirmation would
    // simply be rejected server-side (`confirmation_required`), so this is the
    // affordance for a gate that is enforced regardless.
    if (pendingDangerous.length > 0) {
      setConfirmOpen(true);
      return;
    }
    await submit({});
  };

  const onConfirmed = async () => {
    setConfirmOpen(false);
    await submit(buildConfirmMap(pendingDangerous));
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

      {mutation.error && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }}>
          <span>⚠️</span>
          <span>{mutation.error}</span>
        </Notice>
      )}

      <div style={{ minHeight: 320 }}>
        <DynamicGrid
          layoutKey={`settings-${settingsKey}`}
          defaultLayouts={{ lg: groupLayouts }}
        >
          {data.groups.map((group, idx) => {
            if (group.fields.length === 0) return null;
            const groupDirtyCount = group.fields.filter((f) => edited[f.key] !== baseline[f.key]).length;
            const groupRejectedCount = group.fields.filter((f) => Boolean(rejected[f.key])).length;
            return (
              <div key={`group-${idx}`}>
                <TunableGroupCard
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
                        labelMap={labelMap}
                      />
                    ))}
                  </div>
                </TunableGroupCard>
              </div>
            );
          })}
          {dangerZone && (
            <div key="danger-zone" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
              <div className="drag-handle" style={{ background: "transparent", cursor: "grab", height: "20px", display: "flex", justifyContent: "center", alignItems: "center" }}>
                <div style={{ width: "40px", height: "4px", background: "var(--border)", borderRadius: "2px" }} />
              </div>
              <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
                {dangerZone}
              </div>
            </div>
          )}
        </DynamicGrid>
      </div>

      <div style={{ position: "sticky", bottom: "var(--safe-bottom)", marginTop: "var(--s-3)", padding: "var(--s-3)", background: "var(--surface-glass)", backdropFilter: "blur(12px)", borderTop: "1px solid var(--border)", zIndex: 10, borderRadius: "var(--r-md)" }}>
        <Button variant="primary" block disabled={!canSave} pending={mutation.pending} onClick={doSave}>
          {dirty ? `Save ${dirtyKeys.length} change${dirtyKeys.length === 1 ? "" : "s"}` : "Save changes"}
        </Button>
      </div>

      <AnimatePresence>
        {confirmOpen && (
          <DangerousConfirmDialog
            keys={pendingDangerous}
            fields={flatFields}
            edited={edited}
            pending={mutation.pending}
            onCancel={() => setConfirmOpen(false)}
            onConfirm={onConfirmed}
          />
        )}
      </AnimatePresence>
    </>
  );
}

/**
 * Confirmation for a save touching one or more `DANGEROUS_KEYS` fields.
 *
 * Requires the operator to TYPE each field's name, following this codebase's
 * established type-the-magic-word pattern (`Settings.tsx`'s
 * `RestartDaemonControl`, which types "RESTART"). Typing the name rather than
 * clicking one "yes" is deliberate and mirrors the backend's own contract: the
 * server wants each key echoed by name precisely so confirming one dangerous
 * field can never implicitly confirm a second one in the same batch.
 *
 * This dialog is an affordance, not the safety boundary — the identical gate is
 * enforced in `api/pilots_api.py`, so bypassing the UI gains nothing.
 */
function DangerousConfirmDialog({
  keys,
  fields,
  edited,
  pending,
  onCancel,
  onConfirm,
}: {
  keys: string[];
  fields: TunableField[];
  edited: Record<string, EditVal>;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState<Record<string, string>>({});
  const allConfirmed = keys.every((k) => (typed[k] ?? "").trim() === k);
  const byKey = new Map(fields.map((f) => [f.key, f]));

  // This is a safety-critical confirmation gate -- the operator must see
  // every field they need to confirm quickly, not wait on a slow reveal.
  // `useReducedMotion` mirrors this codebase's existing `@media
  // (prefers-reduced-motion: reduce)` CSS convention (index.css) for the one
  // framer-motion consumer in this app so far: collapse to an instant,
  // no-offset transition rather than skip animating (skipping outright would
  // also skip the `initial` state's opacity/offset, but framer-motion resolves
  // `animate` immediately when `transition.duration` is 0, which reads
  // identically to "no motion" for a reduced-motion user).
  const shouldReduceMotion = useReducedMotion();
  const dialogTransition = { duration: shouldReduceMotion ? 0 : 0.2, ease: "easeOut" as const };
  const rowOffsetY = shouldReduceMotion ? 0 : 6;
  // A subtle stagger, not a slow reveal: ~50ms per row so a 2-3 field batch
  // (the common case) finishes settling well under 250ms total.
  const rowStaggerSeconds = shouldReduceMotion ? 0 : 0.05;

  return (
    <Modal ariaLabel="Confirm safety-critical settings change" onClose={onCancel}>
      <motion.div
        data-testid="dangerous-confirm"
        initial={{ opacity: 0, y: shouldReduceMotion ? 0 : 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: shouldReduceMotion ? 0 : 8 }}
        transition={dialogTransition}
      >
        <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
          {keys.length === 1
            ? "Change a safety-critical setting?"
            : `Change ${keys.length} safety-critical settings?`}
        </h2>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
          {keys.length === 1 ? "This field is" : "These fields are"} part of this
          platform&apos;s safety and execution controls. Confirm each one by typing its
          name exactly.
        </p>

        <AnimatePresence>
          {keys.map((k, i) => {
            const f = byKey.get(k);
            const next = edited[k];
            return (
              <motion.div
                key={k}
                initial={{ opacity: 0, y: rowOffsetY }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{
                  duration: shouldReduceMotion ? 0 : 0.18,
                  delay: i * rowStaggerSeconds,
                  ease: "easeOut",
                }}
                style={{ marginTop: "var(--s-3)" }}
              >
                <p
                  style={{
                    color: theme.textSecondary,
                    fontSize: "var(--t-label)",
                    margin: "0 0 var(--s-1)",
                  }}
                  data-testid={`dangerous-summary-${k}`}
                >
                  <strong>{k}</strong>
                  {" → "}
                  <code>{String(next)}</code>
                  {f?.description ? ` — ${f.description}` : ""}
                </p>
                <Input
                  label={`Type "${k}" to confirm`}
                  value={typed[k] ?? ""}
                  onChange={(e) => setTyped((s) => ({ ...s, [k]: e.target.value }))}
                  hint="Required."
                />
              </motion.div>
            );
          })}
        </AnimatePresence>

        <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
          <Button
            variant="neutral"
            onClick={onCancel}
            style={{ flex: 1 }}
            data-testid="dangerous-confirm-cancel"
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={onConfirm}
            disabled={!allConfirmed}
            pending={pending}
            style={{ flex: 2 }}
            data-testid="dangerous-confirm-yes"
          >
            Save {keys.length === 1 ? "this change" : "these changes"}
          </Button>
        </div>
      </motion.div>
    </Modal>
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

/**
 * One editable field. This is the single place per-field UI is decided for all
 * five editors, so the liveness/safety treatment (the applies badge, the
 * disabled env-pinned input, the dangerous marker) is added HERE, once, off the
 * backend's `liveness` metadata, rather than per screen.
 */
function FieldRow({
  field: f,
  value,
  onChange,
  invalid,
  rejectedReason,
  labelMap,
}: {
  field: TunableField;
  value: EditVal;
  onChange: (v: EditVal) => void;
  invalid: boolean;
  rejectedReason: string | null;
  labelMap?: Record<string, string>;
}) {
  const rangeMsg =
    f.min !== undefined || f.max !== undefined
      ? `Must be a number in [${f.min ?? "−∞"}, ${f.max ?? "∞"}].`
      : "Must be a number.";

  // Only humanize when a screen opts in via `labelMap` (currently just
  // FmpSettings) -- every other editor's tests assert on the raw backend key
  // as the visible label, matching this component's pre-existing contract.
  const textLabel = labelMap ? labelMap[f.key] ?? humanizeKey(f.key) : f.key;
  const inputId = `field-${f.key}`;
  const lv = resolveLiveness(f);
  const badge = fieldBadge(f);
  const editable = isFieldEditable(f);

  // An env-pinned field's own explanation is the most useful thing to say in
  // the input's caption slot, so it replaces the description there. Everything
  // else keeps the description; a restart reason is surfaced separately below
  // so it never displaces the field's own documentation.
  const pinnedCaption = `A shell environment variable is set for ${f.key}, which overrides both .env and the runtime store. Unset it and restart to edit this here.`;
  const hint = !editable ? pinnedCaption : invalid ? rangeMsg : f.description ?? undefined;

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-1-5)",
          flexWrap: "wrap",
          marginBottom: "var(--s-1)",
        }}
      >
        <InfoTip content={badge.title} ariaLabel={`What "${badge.label}" means`}>
          <span
            className={`badge badge-${badge.tone} badge-glass`}
            data-testid={`applies-badge-${f.key}`}
            data-applies={lv.applies}
          >
            {lv.applies === "next_daemon_restart" && <span className="pulse-dot" />}
            {badge.label}
          </span>
        </InfoTip>
        {lv.dangerous && (
          <InfoTip
            content="A safety-critical setting. Saving a change to this field requires typing its name to confirm."
            ariaLabel="What “Safety-critical” means"
          >
            <span className="badge badge-bad" data-testid={`dangerous-badge-${f.key}`}>
              Safety-critical
            </span>
          </InfoTip>
        )}
      </div>

      {f.type === "boolean" ? (
        <>
          <Toggle
            checked={value === true}
            onChange={(v) => onChange(v)}
            label={textLabel}
            disabled={!editable}
          />
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-label)", margin: "var(--s-1-5) 0 0" }}>
            {!editable ? pinnedCaption : f.description}
          </p>
        </>
      ) : f.type === "enum" ? (
        <Select
          id={inputId}
          label={textLabel}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          options={(f.options ?? []).map((o) => ({ value: o, label: o }))}
          disabled={!editable}
          hint={hint}
        />
      ) : isJsonBlob(f) ? (
        <Textarea
          id={inputId}
          label={textLabel}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          spellCheck={false}
          monospace
          invalid={invalid}
          disabled={!editable}
          hint={hint}
        />
      ) : isTagList(f) ? (
        <TagInput
          id={inputId}
          label={textLabel}
          value={typeof value === "string" && value.length > 0 ? value.split(",").map((s) => s.trim()) : []}
          onChange={(arr) => onChange(arr.join(","))}
          invalid={invalid}
          disabled={!editable}
          hint={hint}
        />
      ) : (
        <Input
          id={inputId}
          label={textLabel}
          type={f.type === "number" ? "number" : "text"}
          inputMode={f.type === "number" ? "decimal" : undefined}
          min={f.min}
          max={f.max}
          step={f.step}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          invalid={invalid}
          disabled={!editable}
          hint={hint}
        />
      )}

      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-1-5) 0 0" }}>
        Default: {defaultLabel(f)}
      </p>

      {/*
        Why a restart is needed, with the capture sites that prove it. Shown for
        a field that needs one and is not already explained by an env pin, so
        the claim is checkable by the operator instead of taken on trust.
      */}
      {lv.applies === "next_daemon_restart" && lv.restart_reason && (
        <p
          style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "var(--s-1) 0 0" }}
          data-testid={`restart-reason-${f.key}`}
        >
          {lv.restart_reason}
        </p>
      )}

      {rejectedReason && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2)" }} data-testid={`rejected-${f.key}`}>
          <span>⚠️</span>
          <span>{rejectedReason}</span>
        </Notice>
      )}
    </div>
  );
}
