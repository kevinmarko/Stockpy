import { useApi } from "../hooks/useApi";
import { api } from "../api/client";
import { AutomationStatus } from "../api/types";
import { theme } from "../theme";

export function GlobalStatusBanner() {
  const { data: status } = useApi<AutomationStatus>(() => api.getAutomationStatus(), []);

  if (!status) return null;

  const killSwitchActive = status.kill_switch.active;
  const isAdvisoryOnly = status.advisory_only;

  let bannerColor: string = theme.growth;
  let bannerText = "System Healthy";

  if (killSwitchActive) {
    bannerColor = theme.caution; // Red-ish/Orange in this theme
    bannerText = `Kill Switch Active: ${status.kill_switch.reason}`;
  } else if (isAdvisoryOnly) {
    bannerColor = theme.caution; // Yellow
    bannerText = "Advisory Only Mode (Live Execution Disabled)";
  }

  return (
    <div style={{
      background: bannerColor,
      color: theme.base,
      padding: "var(--s-2) var(--s-4)",
      borderRadius: "var(--r-md)",
      fontWeight: 600,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      marginBottom: "var(--s-2)"
    }}>
      <span>{bannerText}</span>
    </div>
  );
}
