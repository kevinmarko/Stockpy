import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function SettingsPaperBroker() {
  return (
    <GenericSettingsEditor
      title="Paper Broker"
      subtitle="Configure Paper Broker execution backend, slippage, and defaults."
      backTo="/settings"
      fetchSettings={() => api.getPaperBrokerSettings()}
      updateSettings={(values, confirm) => api.updatePaperBrokerSettings(values, confirm)}
    />
  );
}
