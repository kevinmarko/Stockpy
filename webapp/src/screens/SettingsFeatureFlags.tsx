import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function SettingsFeatureFlags() {
  return (
    <GenericSettingsEditor
      title="Feature Flags"
      subtitle="Configure operational feature flags (discovery, execution, RLHF calibration, etc)."
      backTo="/settings"
      fetchSettings={() => api.getFeatureFlags()}
      updateSettings={(values, confirm) => api.updateFeatureFlags(values, confirm)}
    />
  );
}
