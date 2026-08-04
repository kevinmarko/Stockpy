import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function FmpSettings() {
  return (
    <GenericSettingsEditor
      title="Financial Modeling Prep (FMP)"
      subtitle="Configure FMP API credentials, timeouts, retries, primary data feeds (quotes, bars, fundamentals), and diagnostic supplement feeds."
      backTo="/settings"
      fetchSettings={() => api.getFmpSettings()}
      updateSettings={(values, confirm) => api.updateFmpSettings(values, confirm)}
    />
  );
}
