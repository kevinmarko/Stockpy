import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { LlmCapabilityRow, LlmStatus } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { ErrorState, Loading, MetricBadge, StaleDataNotice } from "../components/ui";
import { Toggle } from "../components/Toggle";
import { timeAgo } from "../format";
import { theme } from "../theme";

/**
 * AI Control Center — the write path over GET/PUT /llm/setting for the 5 AI
 * capabilities gui/ai_control_center.py registers (analyst rationale
 * commentary, alert commentary, Gemini chart vision, the Gravity AI runner,
 * Opal research). A `.env`-write surface, so it lives under /settings
 * (reached from the "AI providers" link card), not top-level nav.
 *
 * Three capabilities SHARE `toggle_key: "LLM_COMMENTARY_ENABLED"` -- flipping
 * ONE env key affects all three -- so this screen renders ONE toggle per
 * UNIQUE toggle_key (deduped), not one per capability, with a label listing
 * every capability it covers. Two of those three, plus Opal, additionally
 * carry their own `provider_selector_setting` for flexible per-job routing
 * (Claude or Gemini may serve rationale/alert commentary; OpenAI or Gemini
 * may serve Opal) -- rendered as a provider <select> next to that capability.
 *
 * Every write fails closed server-side when LLM_WRITES_ENABLED is off (the
 * default) -- this screen renders a read-only notice up front (via
 * `data.writable`/`writable_note`) rather than only discovering it from a
 * failed mutation, though a stray 403 is still handled gracefully by
 * useMutation's error surface.
 */
export function AIControlCenter() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/settings"));

  const { data, loading, error, status, stale, cachedAt, reload } = useApi<LlmStatus>(
    () => api.getLlmStatus(),
    []
  );

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
      <h1 className="screen-title">AI Control Center</h1>
      <p className="screen-sub">
        Which LLM capabilities are on, which provider serves each one, and what
        happened on the last real call.
      </p>

      {loading && <Loading lines={5} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <>
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          <CapabilityToggles data={data} onSaved={reload} />
          <TelemetrySection data={data} />
        </>
      )}
    </div>
  );
}

/** One entry per unique toggle_key, in first-seen (registry) order, carrying
 * every capability row that shares it. */
function groupByToggleKey(rows: LlmCapabilityRow[]): { toggleKey: string; rows: LlmCapabilityRow[] }[] {
  const order: string[] = [];
  const groups = new Map<string, LlmCapabilityRow[]>();
  for (const row of rows) {
    if (!row.toggle_key) continue; // read-only row, no writable toggle
    if (!groups.has(row.toggle_key)) {
      groups.set(row.toggle_key, []);
      order.push(row.toggle_key);
    }
    groups.get(row.toggle_key)!.push(row);
  }
  return order.map((toggleKey) => ({ toggleKey, rows: groups.get(toggleKey)! }));
}

/**
 * Per-capability allowed provider choices. Matches
 * gui/ai_control_center.py's real registry (`provider_key_settings` +
 * `_PROVIDER_KEY_MAP`), not a generic "offer all 4" -- claude_commentary /
 * gemini_alerts flex between Claude and Gemini only; opal_research flexes
 * between OpenAI and Gemini only.
 */
const PROVIDER_OPTIONS: Record<string, { value: string; label: string }[]> = {
  claude_commentary: [
    { value: "claude", label: "Claude" },
    { value: "gemini", label: "Gemini" },
    { value: "none", label: "None (off)" },
  ],
  gemini_alerts: [
    { value: "claude", label: "Claude" },
    { value: "gemini", label: "Gemini" },
    { value: "none", label: "None (off)" },
  ],
  opal_research: [
    { value: "openai", label: "OpenAI" },
    { value: "gemini", label: "Gemini" },
    { value: "none", label: "None (off)" },
  ],
};

function CapabilityToggles({ data, onSaved }: { data: LlmStatus; onSaved: () => void }) {
  const groups = groupByToggleKey(data.capabilities);

  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      {!data.writable && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <span aria-hidden>⚠️</span>
          <span>{data.writable_note}</span>
        </div>
      )}
      <div>
        {groups.map((g) => (
          <ToggleGroupRow key={g.toggleKey} toggleKey={g.toggleKey} rows={g.rows} writable={data.writable} onSaved={onSaved} />
        ))}
      </div>
    </section>
  );
}

function ToggleGroupRow({
  toggleKey,
  rows,
  writable,
  onSaved,
}: {
  toggleKey: string;
  rows: LlmCapabilityRow[];
  writable: boolean;
  onSaved: () => void;
}) {
  // A row with no provider_selector_setting mirrors the raw toggle_key value
  // exactly (it has no "and provider != none" AND'd in); prefer it as the
  // ground truth for the group's displayed state when one exists. Otherwise
  // fall back to "any member enabled" -- true implies the master switch is
  // on (enabled = master AND provider != none), so this can only ever read
  // as OFF when it's actually ON in the corner case where every member in
  // the group carries a provider_selector_setting currently set to "none".
  const groundTruth = rows.find((r) => r.provider_selector_setting == null);
  const checked = groundTruth ? groundTruth.enabled : rows.some((r) => r.enabled);

  const mutation = useMutation((next: boolean) => api.putLlmSetting(toggleKey, next));

  const labels = rows.map((r) => r.label).join(" + ");

  const handleToggle = async (next: boolean) => {
    await mutation.run(next);
    onSaved();
  };

  return (
    <div style={{ marginBottom: 14, paddingBottom: 14, borderBottom: "1px solid var(--border)" }}>
      <Toggle
        checked={checked}
        onChange={handleToggle}
        label={labels}
        disabled={!writable}
        pending={mutation.pending}
      />
      <p style={{ color: theme.textMuted, fontSize: 12, margin: "4px 0 0" }}>
        <code>{toggleKey}</code>
        {rows.length > 1 ? " -- covers all capabilities listed above." : ""}
      </p>
      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 8 }}>
          <span aria-hidden>⚠️</span>
          <span>{mutation.error}</span>
        </div>
      )}
      {mutation.result && !mutation.error && (
        <p style={{ color: theme.textMuted, fontSize: 12, margin: "6px 0 0" }}>
          {mutation.result.note}
        </p>
      )}

      {rows
        .filter((r) => r.provider_selector_setting)
        .map((r) => (
          <ProviderSelectRow key={r.key} row={r} writable={writable} onSaved={onSaved} />
        ))}
    </div>
  );
}

function ProviderSelectRow({
  row,
  writable,
  onSaved,
}: {
  row: LlmCapabilityRow;
  writable: boolean;
  onSaved: () => void;
}) {
  const settingKey = row.provider_selector_setting as string;
  const options = PROVIDER_OPTIONS[row.key] ?? [
    { value: "claude", label: "Claude" },
    { value: "gemini", label: "Gemini" },
    { value: "openai", label: "OpenAI" },
    { value: "none", label: "None (off)" },
  ];
  const current = row.active_provider ?? "none";

  const mutation = useMutation((value: string) => api.putLlmSetting(settingKey, value));

  const handleChange = async (value: string) => {
    await mutation.run(value);
    onSaved();
  };

  return (
    <div style={{ marginTop: 10, marginLeft: 4 }}>
      <label style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12.5, color: theme.textMuted }}>{row.label} provider</span>
        <select
          value={current}
          disabled={!writable || mutation.pending}
          onChange={(e) => handleChange(e.target.value)}
          aria-label={`${row.label} provider`}
          style={{
            fontSize: 14,
            padding: "6px 10px",
            borderRadius: "var(--r-md)",
            background: "var(--surface-2)",
            color: theme.textPrimary,
            border: "1px solid var(--border)",
          }}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {mutation.pending && (
          <span style={{ fontSize: 12, color: theme.textMuted }}>Saving…</span>
        )}
      </label>
      {mutation.error && (
        <div className="notice notice-warn" style={{ marginTop: 6 }}>
          <span aria-hidden>⚠️</span>
          <span>{mutation.error}</span>
        </div>
      )}
    </div>
  );
}

/** Badge label per capability status (the Streamlit STATUS_BADGE analogue). */
const LLM_BADGE_LABEL: Record<LlmCapabilityRow["status"], string> = {
  ready: "Ready",
  disabled: "Off",
  missing_key: "Key missing",
  invalid_key: "Key rejected",
  not_built: "Not built",
};

/**
 * Last-real-call telemetry per capability -- moved here from Settings.tsx's
 * former inline `LlmStatusSection` (see App.tsx / Settings.tsx history) so
 * the write controls above and the read-only diagnostics that explain WHY a
 * capability isn't producing narratives live on the same screen. The
 * platform never probes a provider to test a key, so a null verdict means
 * "no call has been made with the current key yet", NOT "broken". All copy
 * is past-tense + timestamped.
 */
function TelemetrySection({ data }: { data: LlmStatus }) {
  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>Provider telemetry</h2>
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
        What happened on the last real call to each provider.
      </p>
      <div className="list">
        {data.capabilities.map((c) => {
          const tel = c.active_provider ? data.providers[c.active_provider] : null;
          // disabled / not_built are a deliberate "off" -> neutral, never a warning.
          const good =
            c.status === "ready"
              ? true
              : c.status === "invalid_key" || c.status === "missing_key"
                ? false
                : null;
          return (
            <div key={c.key} style={{ marginBottom: 6 }}>
              <div className="row">
                <span className="row-title">{c.label}</span>
                <MetricBadge
                  label={LLM_BADGE_LABEL[c.status]}
                  value={c.active_provider ?? c.provider_keys.join(", ")}
                  good={good}
                />
              </div>
              {c.status === "invalid_key" && c.invalid_provider && (
                <div className="notice notice-warn" style={{ marginTop: 8 }}>
                  <span aria-hidden>⚠️</span>
                  <span>
                    The last real {c.invalid_provider} call
                    {tel?.checked_at ? ` (${timeAgo(tel.checked_at)})` : ""} was rejected as
                    unauthenticated. Check <code>{c.provider_keys.join(", ")}</code> in{" "}
                    <code>.env</code>. This clears automatically on the next successful call, or
                    as soon as the key is changed.
                  </span>
                </div>
              )}
              {c.status === "missing_key" && (
                <div className="notice notice-warn" style={{ marginTop: 8 }}>
                  <span aria-hidden>⚠️</span>
                  <span>
                    Enabled, but <code>{c.provider_keys.join(", ")}</code> is unset in{" "}
                    <code>.env</code>. Narratives fall back to the deterministic template.
                  </span>
                </div>
              )}
              {tel?.source === "key_rotated" && (
                <p style={{ color: theme.textMuted, fontSize: 12, margin: "4px 0 0" }}>
                  Key changed since the last recorded call — no telemetry for the current key yet.
                </p>
              )}
              {tel?.source === "last_call" && tel.ok === false && c.status !== "invalid_key" && (
                <p style={{ color: theme.textMuted, fontSize: 12, margin: "4px 0 0" }}>
                  Last call failed: {tel.error_kind}
                  {tel.checked_at ? ` · ${timeAgo(tel.checked_at)}` : ""} (not a key problem).
                </p>
              )}
            </div>
          );
        })}
        <p
          style={{
            color: theme.textMuted,
            fontSize: "var(--t-caption)",
            marginTop: 12,
            lineHeight: 1.5,
          }}
        >
          {data.telemetry_note}
        </p>
      </div>
    </section>
  );
}
