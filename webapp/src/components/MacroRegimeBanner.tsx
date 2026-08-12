import { api } from "../api/client";
import type { ObservabilitySummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { EmptyState, ErrorState, Loading, Notice } from "./ui";
import { fmtNum } from "../format";
import { theme } from "../theme";

/**
 * Macro-regime banner widget -- self-contained (fetches its own data via
 * `api.getObservabilitySummary()`, no required props) so it can be dropped
 * into any screen, including the Create Data App `/app/:slug` renderer.
 *
 * Reads ONLY the `regime` (`RegimeOverlay`) key of `ObservabilitySummary`.
 * Renders a single-row summary (market regime + VIX) plus a warn-variant
 * `Notice` when a macro kill event is BOTH real AND enforced -- per
 * `RegimeOverlay.macro_kill_switch`'s own doc comment, new BUY orders are
 * actually paused for macro reasons only when `macro_kill_switch` AND
 * `macro_regime_gate_enabled` are BOTH `true`. `kill_switch_active` (the
 * operator's separate, manual global kill-switch file) is a DIFFERENT
 * mechanism and is deliberately never read here -- conflating the two would
 * misreport why (or whether) new orders are actually paused.
 */
export function MacroRegimeBanner() {
  const { data, loading, error, status, reload } = useApi<ObservabilitySummary>(
    () => api.getObservabilitySummary("1M", 30),
    []
  );

  if (loading) return <Loading lines={1} />;
  if (error) return <ErrorState message={error} status={status} onRetry={reload} />;

  const regime = data?.regime;
  if (!regime || regime.reason) {
    return (
      <EmptyState
        title="No macro-regime telemetry yet"
        hint={regime?.reason ?? "Populates once a pipeline cycle writes a state snapshot."}
      />
    );
  }

  const macroPaused = regime.macro_kill_switch === true && regime.macro_regime_gate_enabled === true;

  return (
    <div data-testid="macroRegime-widget">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--s-4)",
          flexWrap: "wrap",
        }}
      >
        <div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>Market Regime</div>
          <div style={{ fontSize: "var(--t-subhead)", fontWeight: 700 }}>
            {regime.market_regime ?? "—"}
          </div>
        </div>
        <div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>VIX</div>
          <div style={{ fontSize: "var(--t-subhead)", fontWeight: 700 }}>{fmtNum(regime.vix, 1)}</div>
        </div>
      </div>

      {macroPaused && (
        <Notice variant="warn" style={{ marginTop: "var(--s-3)" }} data-testid="macroRegime-kill-switch-notice">
          <span>Macro kill switch active — new BUY orders are paused.</span>
        </Notice>
      )}
    </div>
  );
}
