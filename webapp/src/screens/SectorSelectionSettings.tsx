import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function SectorSelectionSettings() {
  return (
    <GenericSettingsEditor
      title="Sector Selection"
      settingsKey="sector-selection"
      subtitle="Configure sector selection parameters (top N, weights, lookbacks, momentum/value/volatility thresholds)."
      backTo="/settings"
      fetchSettings={() => api.getSectorSelectionSettings()}
      updateSettings={(values, confirm) => api.updateSectorSelectionSettings(values, confirm)}
    />
  );
}
