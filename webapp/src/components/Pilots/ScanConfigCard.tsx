import { api } from "../../api/client";
import { useApi } from "../../hooks/useApi";
import { useMutation } from "../../hooks/useMutation";
import type { AgenticDiscovery, ScanConfig } from "../../api/types";
import { Chip, EmptyState, ErrorState, Loading, Notice, StaleDataNotice } from "../ui";
import { theme } from "../../theme";

/**
 * Pilots Manager's scanner-profile card -- a read + toggle view of the SAME
 * scan configs AgenticTrading.tsx's DiscoverySection manages, sourced from
 * GET /agentic/discovery's `.scan_configs` and written back through
 * PUT /agentic/scan-config (full-row replace semantics -- toggling `enabled`
 * resends the config's own unchanged `filters`, never a fabricated/empty
 * filter set). This is deliberately NOT a reimplementation of that screen's
 * full ScanConfigModal -- creating a brand-new scan config still lives there;
 * this card only lists what already exists and lets the operator flip each
 * one's enabled bit.
 */
export function ScanConfigCard() {
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<AgenticDiscovery>(
    () => api.getAgenticDiscovery(),
    []
  );

  return (
    <div className="card card-pad">
      <div className="card-header">
        <h3 className="card-title" style={{ margin: 0 }}>
          Scanner Profiles
        </h3>
      </div>
      <div className="card-content" style={{ marginTop: "var(--s-2)" }}>
        {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
        {loading && <Loading lines={2} />}
        {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
        {!loading && !error && data && data.scan_configs.length === 0 && (
          <EmptyState
            title="No scan configs yet"
            hint="Add one from the Agentic Trading screen's Discovery section."
          />
        )}
        {!loading && !error && data && data.scan_configs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
            {data.scan_configs.map((cfg) => (
              <ScanConfigRow key={cfg.name} cfg={cfg} writable={data.writable} onChanged={reload} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ScanConfigRow({
  cfg,
  writable,
  onChanged,
}: {
  cfg: ScanConfig;
  writable: boolean;
  onChanged: () => void;
}) {
  const toggle = useMutation(() =>
    api.putScanConfig({ name: cfg.name, filters: cfg.filters, enabled: !cfg.enabled })
  );

  const handleToggle = async () => {
    const r = await toggle.run();
    if (r) onChanged();
  };

  return (
    <div
      data-testid="scan-config-row"
      style={{
        padding: "var(--s-2-5) var(--s-3)",
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: "var(--r-sm)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--s-1-5)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--s-2)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <Chip label={cfg.enabled ? "enabled" : "disabled"} tone={cfg.enabled ? "growth" : "muted"} />
          <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: "var(--t-body)" }}>
            {cfg.name}
          </span>
        </div>
        {writable ? (
          <button
            className="btn btn-neutral"
            onClick={handleToggle}
            disabled={toggle.pending}
            style={{ padding: "4px 10px", fontSize: "var(--t-caption)" }}
          >
            {toggle.pending ? "…" : cfg.enabled ? "Disable" : "Enable"}
          </button>
        ) : (
          <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>read-only</span>
        )}
      </div>
      {toggle.error && (
        <Notice variant="warn">
          <span>⚠️</span>
          <span>{toggle.error}</span>
        </Notice>
      )}
    </div>
  );
}
