import { usePwaStatus } from "../hooks/usePwaStatus";
import { theme } from "../theme";
import { Button } from "./ui";

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
 * App status card — service-worker registration state, offline-cache
 * readiness, and pending-update telemetry, with a one-click "reload to
 * update" action. Formerly a standalone bottom-sheet (PwaStatusDrawer,
 * opened by a floating ⚙ button on every screen); folded into a plain
 * section on the Settings screen so ⚙ means one thing (Settings) instead of
 * two competing "settings" affordances. The content and usePwaStatus() wiring
 * are unchanged from the drawer — only the presentation (card, not a modal)
 * and location (embedded in Settings.tsx) changed.
 */
export function PwaStatusSection() {
  const pwa = usePwaStatus();

  return (
    <div className="card card-pad">
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>App status</h2>
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
        Service worker &amp; offline-cache telemetry for this installed app.
      </p>

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

      {pwa.needRefresh && (
        <>
          <div className="notice notice-warn" style={{ marginTop: 14 }}>
            <span>⚠️</span>
            <span>
              A new version has been downloaded and is ready to install. Reload to
              switch to it.
            </span>
          </div>
          <Button
            variant="primary"
            block
            onClick={pwa.update}
            data-testid="pwa-update-btn"
            style={{ marginTop: 12 }}
          >
            Reload to update
          </Button>
        </>
      )}
    </div>
  );
}
