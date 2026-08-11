import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function SettingsCacheLongShort() {
  return (
    <GenericSettingsEditor
      title="Cache Long/Short Strategy"
      subtitle="Configure Cache Long/Short strategy parameters, thresholds, and limits."
      backTo="/settings"
      fetchSettings={() => api.getCacheLongShortSettings()}
      updateSettings={(values, confirm) => api.updateCacheLongShortSettings(values, confirm)}
    />
  );
}
