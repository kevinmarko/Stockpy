import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function FeatureFlagsScreen() {
  return (
    <GenericSettingsEditor
      title="Feature Flags"
      subtitle="Configure admin, write, and execution gates, along with diagnostic and data features."
      backTo="/settings"
      fetchSettings={() => api.getFeatureFlags()}
      updateSettings={(values, confirm) => api.updateFeatureFlags(values, confirm)}
    />
  );
}
