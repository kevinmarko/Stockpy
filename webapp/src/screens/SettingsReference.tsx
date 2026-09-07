import { useState, useMemo } from "react";
import { Link } from "react-router";
import toast from "react-hot-toast";
import { api } from "../api/client";
import type { SettingsReferenceField, SettingsReferenceResponse } from "../api/types";
import { useApi } from "../hooks/useApi";
import { Loading, ErrorState, Button, Input } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { Toggle } from "../components/Toggle";
import { Modal } from "../components/Modal";
import { appliesBadge, saveOutcomeMessage } from "../settingsLiveness";
import { theme } from "../theme";

/**
 * Confirmation for toggling ONE `dangerous` (`settings_keysets.DANGEROUS_KEYS`)
 * boolean field. A single-field, lighter-weight sibling of
 * `GenericSettingsEditor.tsx`'s `DangerousConfirmDialog` (that one is built
 * for a batch of edited fields from a form's dirty-state, which this screen
 * has no equivalent of — each field here saves immediately on its own). Same
 * safety pattern regardless: type the field's exact name, never a bare "yes"
 * — the real gate is enforced server-side either way, this is affordance.
 */
function ReferenceDangerousConfirmDialog({
  field,
  next,
  pending,
  onCancel,
  onConfirm,
}: {
  field: SettingsReferenceField;
  next: boolean;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const confirmed = typed.trim() === field.key;

  return (
    <Modal ariaLabel="Confirm safety-critical settings change" onClose={onCancel}>
      <div data-testid="reference-dangerous-confirm">
        <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
          Turn {field.key} {next ? "on" : "off"}?
        </h2>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
          This field is part of this platform&apos;s safety and execution controls.
          {field.description ? ` ${field.description}` : ""}
        </p>
        <Input
          label={`Type "${field.key}" to confirm`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          hint="Required."
        />
        <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4)" }}>
          <Button
            variant="neutral"
            onClick={onCancel}
            style={{ flex: 1 }}
            data-testid="reference-dangerous-confirm-cancel"
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={onConfirm}
            disabled={!confirmed}
            pending={pending}
            style={{ flex: 2 }}
            data-testid="reference-dangerous-confirm-yes"
          >
            {next ? "Turn on" : "Turn off"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function SettingsReference() {
  const { data, loading, error, status, reload } = useApi<SettingsReferenceResponse>(
    () => api.getSettingsReference(),
    []
  );

  const [search, setSearch] = useState("");
  const [selectedDomain, setSelectedDomain] = useState<string>("ALL");
  // A dangerous field's Toggle never writes directly (see handleToggle below)
  // -- it opens this instead, and the confirm dialog performs the real write.
  const [pendingConfirm, setPendingConfirm] = useState<{ field: SettingsReferenceField; next: boolean } | null>(null);
  const [confirmSaving, setConfirmSaving] = useState(false);

  const fields = data?.fields ?? [];
  const domains = data?.domains ?? [];

  /**
   * A writable, non-dangerous field's Toggle calls this directly. Throwing
   * here is what makes `Toggle` revert its optimistic flip and show an error
   * toast -- see `Toggle`'s own `handleChange`.
   */
  async function handleToggle(field: SettingsReferenceField, next: boolean) {
    if (field.dangerous) {
      // Open the confirm dialog instead of writing. Returning normally (not
      // throwing) lets `Toggle`'s own effect snap the switch back to the
      // real current value once this resolves -- no premature flip, no
      // misleading "update failed" toast for what is a pending confirmation,
      // not a failure.
      setPendingConfirm({ field, next });
      return;
    }
    const result = await api.updateSettingsReference({ [field.key]: next });
    const reason = result.rejected?.[field.key];
    if (reason) {
      throw new Error(`Could not update ${field.key}: ${reason}`);
    }
    const outcome = saveOutcomeMessage(result);
    if (outcome) toast.success(outcome.text);
    await reload();
  }

  async function handleConfirmedToggle() {
    if (!pendingConfirm) return;
    const { field, next } = pendingConfirm;
    setConfirmSaving(true);
    try {
      const result = await api.updateSettingsReference(
        { [field.key]: next },
        { [field.key]: field.key },
      );
      const reason = result.rejected?.[field.key];
      if (reason) {
        toast.error(`Could not update ${field.key}: ${reason}`);
      } else {
        const outcome = saveOutcomeMessage(result);
        if (outcome) toast.success(outcome.text);
        await reload();
      }
      setPendingConfirm(null);
    } finally {
      setConfirmSaving(false);
    }
  }

  const filteredFields = useMemo(() => {
    const q = search.trim().toLowerCase();
    return fields.filter((f) => {
      if (selectedDomain !== "ALL" && f.domain !== selectedDomain) {
        return false;
      }
      if (!q) return true;
      const keyMatch = f.key.toLowerCase().includes(q);
      const descMatch = f.description ? f.description.toLowerCase().includes(q) : false;
      const valMatch = f.value !== null && f.value !== undefined ? String(f.value).toLowerCase().includes(q) : false;
      return keyMatch || descMatch || valMatch;
    });
  }, [fields, search, selectedDomain]);

  const groupedByDomain = useMemo(() => {
    const map = new Map<string, SettingsReferenceField[]>();
    for (const f of filteredFields) {
      const list = map.get(f.domain) ?? [];
      list.push(f);
      map.set(f.domain, list);
    }
    return map;
  }, [filteredFields]);

  if (loading) return <Loading />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)", paddingBottom: "var(--s-8)" }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", marginBottom: "var(--s-1)" }}>
          <Link
            to="/settings"
            style={{
              color: theme.textMuted,
              textDecoration: "none",
              fontSize: "var(--t-body)",
              display: "inline-flex",
              alignItems: "center",
            }}
          >
            ← Settings
          </Link>
        </div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Settings Reference</h2>
        <p style={{ color: theme.textSecondary, margin: 0, fontSize: "var(--t-body)" }}>
          Platform-wide explainer and reference for all {data?.total ?? 0} configuration fields across 14 functional domains.
        </p>
      </div>

      <TabGuide tabKey="settings-reference" />

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-3)", alignItems: "center" }}>
        <div style={{ flex: "1 1 240px" }}>
          <input
            className="input"
            placeholder="Search key, description, or value..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            data-testid="settings-reference-search"
            style={{ width: "100%" }}
          />
        </div>
        <div style={{ flex: "0 0 auto" }}>
          <select
            value={selectedDomain}
            onChange={(e) => setSelectedDomain(e.target.value)}
            data-testid="settings-reference-domain-filter"
            style={{
              padding: "var(--s-2) var(--s-3)",
              background: theme.surface2,
              color: theme.textPrimary,
              border: `1px solid ${theme.border}`,
              borderRadius: "var(--r-md)",
              fontSize: "var(--t-body)",
            }}
          >
            <option value="ALL">All Domains ({fields.length})</option>
            {domains.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
        Showing {filteredFields.length} of {fields.length} settings
      </div>

      {groupedByDomain.size === 0 ? (
        <div className="card card-pad" style={{ textAlign: "center", color: theme.textMuted }}>
          No settings match your search criteria.
        </div>
      ) : (
        Array.from(groupedByDomain.entries()).map(([domainName, domainFields]) => (
          <div key={domainName} className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: `1px solid ${theme.border}`, paddingBottom: "var(--s-2)" }}>
              <h3 style={{ margin: 0, fontSize: "var(--t-body)", fontWeight: 700, color: theme.textPrimary }}>
                {domainName}
              </h3>
              <span style={{ fontSize: "var(--t-caption)", color: theme.textMuted }}>
                {domainFields.length} field{domainFields.length === 1 ? "" : "s"}
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
              {domainFields.map((field) => {
                const badge = appliesBadge(field.liveness.applies);
                const isSecret = field.category === "secret";
                const isNoOp = field.liveness.applies === "no_effect";

                return (
                  <div
                    key={field.key}
                    data-testid={`field-row-${field.key}`}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "var(--s-1-5)",
                      borderBottom: `1px dashed ${theme.border}`,
                      paddingBottom: "var(--s-3)",
                    }}
                  >
                    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: "var(--s-2)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
                        <code
                          style={{
                            fontWeight: 700,
                            fontSize: "var(--t-body)",
                            color: theme.accent,
                            background: theme.surface2,
                            padding: "2px 6px",
                            borderRadius: "var(--r-sm)",
                          }}
                        >
                          {field.key}
                        </code>
                        <span className={`badge badge-${badge.tone}`} title={badge.title}>
                          {badge.label}
                        </span>
                        {field.dangerous && (
                          <span className="badge badge-warn" title="High-stakes configuration requires explicit confirmation on write">
                            Dangerous
                          </span>
                        )}
                        {isSecret && (
                          <span className="badge badge-bad" title="Secret credential — masked from GUI display and logs">
                            Secret
                          </span>
                        )}
                      </div>

                      {field.editable_at && (
                        <Link
                          to={field.editable_at}
                          style={{
                            fontSize: "var(--t-caption)",
                            color: theme.accent,
                            textDecoration: "none",
                            fontWeight: 600,
                          }}
                          data-testid={`edit-link-${field.key}`}
                        >
                          Edit here →
                        </Link>
                      )}
                    </div>

                    <div style={{ fontSize: "var(--t-body)", color: theme.textSecondary, lineHeight: 1.4 }}>
                      {field.description ?? <span style={{ color: theme.textMuted, fontStyle: "italic" }}>No description provided</span>}
                    </div>

                    {isNoOp && (
                      <div style={{ fontSize: "var(--t-caption)", color: theme.caution, background: theme.surface2, padding: "4px 8px", borderRadius: "var(--r-sm)" }}>
                        ⚠️ This field is not read anywhere in active production code — changing it has no effect.
                      </div>
                    )}

                    {field.writable && (
                      <Toggle
                        checked={Boolean(field.value)}
                        onChange={(next) => handleToggle(field, next)}
                        label={field.value ? "On" : "Off"}
                        dataTestId={`toggle-${field.key}`}
                      />
                    )}

                    <div
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "var(--s-4)",
                        fontSize: "var(--t-caption)",
                        color: theme.textMuted,
                        marginTop: "var(--s-0-5)",
                      }}
                    >
                      <div>
                        <span>Current: </span>
                        <code style={{ color: isSecret ? theme.textMuted : theme.textPrimary }}>
                          {isSecret ? field.value : String(field.value)}
                        </code>
                      </div>
                      <div>
                        <span>Default: </span>
                        <code style={{ color: theme.textSecondary }}>
                          {field.default === null || field.default === undefined ? "null" : String(field.default)}
                        </code>
                      </div>
                      <div>
                        <span>Type: </span>
                        <span style={{ color: theme.textSecondary }}>{field.type}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))
      )}

      {pendingConfirm && (
        <ReferenceDangerousConfirmDialog
          field={pendingConfirm.field}
          next={pendingConfirm.next}
          pending={confirmSaving}
          onCancel={() => setPendingConfirm(null)}
          onConfirm={handleConfirmedToggle}
        />
      )}
    </div>
  );
}
