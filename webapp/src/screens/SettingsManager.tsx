import { useState } from "react";
import { api } from "../api/client";
import { clearAllCacheEntries } from "../api/offlineCache";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";
import { Button, Notice } from "../components/ui";
import { Modal } from "../components/Modal";
import { theme } from "../theme";

/**
 * Settings Manager — read + edit the platform's general runtime tunables
 * (GET/PUT /settings/tunables). A `.env`-write surface, so it lives under
 * /settings, reached from the "Runtime tunables" card, mirroring how Strategy
 * Matrix and the AI Control Center each got their own /settings sub-route once
 * they grew a write path.
 *
 * The editor itself — dirty tracking, save-only-changed-keys, per-key rejection
 * surfacing, the widget-per-field-type logic and the "applies on next restart"
 * notice — is `GenericSettingsEditor`, shared with the four scoped editors
 * (/settings/sentiment, /settings/sector-selection, /settings/fmp,
 * /settings/etf-transmission). This screen used to carry its own near-verbatim
 * fork of all of that; it no longer does. What is left here is only what is
 * genuinely specific to the general-tunables screen: its title/subtitle, its
 * own empty-state wording, and the Danger Zone below.
 */
export function SettingsManager() {
  return (
    <GenericSettingsEditor
      title="Runtime tunables"
      subtitle="General platform settings (sizing, forecasting, data). Advisory only — tuning changes what the platform computes and recommends, never places an order."
      backTo="/settings"
      fetchSettings={() => api.getTunables()}
      updateSettings={(values) => api.updateTunables(values)}
      emptyTitle="No tunables exposed"
      emptyHint="The backend returned no editable settings. Nothing here is fabricated when a value is unavailable."
      dangerZone={<DangerZone />}
    />
  );
}

/**
 * Danger Zone — irreversible actions, deliberately scoped to this screen only
 * (the four narrower settings editors do not get one).
 *
 * "Clear Data Cache" clears this browser's localStorage-backed offline-response
 * cache (webapp/src/api/offlineCache.ts). There is no server-side
 * cache-clearing endpoint for this button to call; this IS the one cache the
 * webapp itself owns and can honestly clear. See clearAllCacheEntries()'s
 * docstring for why a failure here is surfaced rather than swallowed.
 */
function DangerZone() {
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

  return (
    <>
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
    </>
  );
}
