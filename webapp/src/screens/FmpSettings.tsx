import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

const FMP_LABEL_MAP: Record<string, string> = {
  FMP_BASE_URL: "Base URL",
  FMP_TIMEOUT_SECONDS: "Timeout (seconds)",
  FMP_MIN_REQUEST_INTERVAL_SECONDS: "Min Request Interval (s)",
  FMP_MAX_RETRIES: "Max Retries",
  FMP_RETRY_BACKOFF_SECONDS: "Retry Backoff (s)",
  FMP_COOLDOWN_THRESHOLD: "Cooldown Threshold (errors)",
  FMP_COOLDOWN_SECONDS: "Cooldown Duration (s)",
  FMP_FALLBACK_ENABLED: "Enable Fallback Mechanism",
  FMP_MAX_SECONDS_PER_CYCLE: "Max Seconds Per Cycle",
  FMP_QUOTES_ENABLED: "Enable Quotes Feed",
  FMP_QUOTES_REALTIME: "Real-time Quotes",
  FMP_BARS_ENABLED: "Enable Bars Feed",
  FMP_BARS_ADJUSTMENT: "Bars Adjustment Mode",
  FMP_FUNDAMENTALS_ENABLED: "Enable Fundamentals Feed",
  FMP_ANALYST_ENABLED: "Enable Analyst Feed",
  FMP_ANALYST_REFRESH_HOURS: "Analyst Refresh Interval (hrs)",
  FMP_EARNINGS_ENABLED: "Enable Earnings Feed",
  FMP_EARNINGS_REFRESH_HOURS: "Earnings Refresh Interval (hrs)",
  FMP_MACRO_ENABLED: "Enable Macroeconomic Feed",
  FMP_ECON_INDICATORS: "Macro Indicators (Comma-separated)",
  FMP_INSIDER_ENABLED: "Enable Insider Trading Feed",
  FMP_INSIDER_REFRESH_DAYS: "Insider Trading Refresh Interval (days)",
  FMP_INSIDER_MIN_LAG_DAYS: "Insider Trading Min Lag (days)",
  FMP_SECTOR_SNAPSHOT_ENABLED: "Enable Sector Snapshot",
};

export function FmpSettings() {
  return (
    <GenericSettingsEditor
      title="Financial Modeling Prep (FMP)"
      subtitle="Configure FMP API credentials, timeouts, retries, primary data feeds (quotes, bars, fundamentals), and diagnostic supplement feeds."
      backTo="/settings"
      fetchSettings={() => api.getFmpSettings()}
      updateSettings={(values, confirm) => api.updateFmpSettings(values, confirm)}
      labelMap={FMP_LABEL_MAP}
    />
  );
}
