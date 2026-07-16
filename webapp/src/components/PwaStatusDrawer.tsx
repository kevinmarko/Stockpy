import { useState } from "react";
import { usePwaStatus } from "../hooks/usePwaStatus";
import { theme } from "../theme";

function StatusRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "good" | "warn" | "neutral";
}) {
  const color =
    tone === "good" ? theme.growth : tone === "warn" ? theme.caution : theme.textMuted;
  const dot = tone === "good" ? "●" : tone === "warn" ? "●" : "○";
  return (
    <div
      className="row"
      style={{ padding: "10px 0", display: "flex", justifyContent: "space-between" }}
    >
      <span className="row-title">{label}</span>
      <span style={{ color, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
        <span aria-hidden>{dot}</span>
        {value}
      </span>
    </div>
  );
}

/**
 * Operator-visible service-worker status drawer (Web App Resilience gap:
 * "no operator UI feedback indicating whether [service workers] are active,
 * caching successfully, or running on the latest updated version").
 *
 * A small persistent trigger (bottom-right, every screen) opens a bottom
 * sheet — reusing the app's existing `.sheet-backdrop`/`.sheet` modal pattern
 * (see FollowModal) — reporting registration state, offline-cache readiness,
 * and pending updates, with a one-click "reload to update" action.
 */
export function PwaStatusDrawer() {
  const [open, setOpen] = useState(false);
  const pwa = usePwaStatus();

  return (
    <>
      <button
        className="btn"
        onClick={() => setOpen(true)}
        aria-label="PWA status"
        data-testid="pwa-status-trigger"
        style={{
          position: "fixed",
          right: 16,
          bottom: 76, // clears the mobile bottom-nav; desktop has no bottom-nav so this just floats
          zIndex: 40,
          width: 40,
          height: 40,
          borderRadius: "50%",
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: theme.surface2,
          border: `1px solid ${theme.borderStrong}`,
        }}
      >
        <span aria-hidden style={{ fontSize: 16 }}>
          ⚙
        </span>
        {pwa.needRefresh && (
          <span
            aria-hidden
            data-testid="pwa-update-dot"
            style={{
              position: "absolute",
              top: 2,
              right: 2,
              width: 9,
              height: 9,
              borderRadius: "50%",
              background: theme.caution,
              border: `2px solid ${theme.base}`,
            }}
          />
        )}
      </button>

      {open && (
        <div
          className="sheet-backdrop"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="PWA status"
          data-testid="pwa-status-sheet"
        >
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-grip" />
            <h2 style={{ margin: "0 0 2px", fontSize: 20 }}>App status</h2>
            <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
              Service worker &amp; offline-cache telemetry for this installed app.
            </p>

            <div className="card card-pad" style={{ padding: "2px 14px", marginTop: 12 }}>
              <div className="list">
                {!pwa.supported ? (
                  <StatusRow label="Service worker" value="Not supported" tone="neutral" />
                ) : pwa.registerError ? (
                  <StatusRow label="Service worker" value="Registration failed" tone="warn" />
                ) : (
                  <StatusRow
                    label="Service worker"
                    value={pwa.registered ? "Active" : "Registering…"}
                    tone={pwa.registered ? "good" : "neutral"}
                  />
                )}
                <StatusRow
                  label="Offline cache"
                  value={pwa.offlineReady ? "Ready for offline use" : "Not cached yet"}
                  tone={pwa.offlineReady ? "good" : "neutral"}
                />
                <StatusRow
                  label="App version"
                  value={pwa.needRefresh ? "Update available" : "Up to date"}
                  tone={pwa.needRefresh ? "warn" : "good"}
                />
              </div>
            </div>

            {pwa.needRefresh && (
              <div className="notice notice-warn" style={{ marginTop: 14 }}>
                <span>⚠️</span>
                <span>
                  A new version has been downloaded and is ready to install. Reload to
                  switch to it.
                </span>
              </div>
            )}

            <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
              <button className="btn" style={{ flex: 1 }} onClick={() => setOpen(false)}>
                Close
              </button>
              {pwa.needRefresh && (
                <button
                  className="btn btn-primary"
                  style={{ flex: 2 }}
                  onClick={pwa.update}
                  data-testid="pwa-update-btn"
                >
                  Reload to update
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
