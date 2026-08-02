import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function EtfTransmissionSettings() {
  return (
    <GenericSettingsEditor
      title="ETF Volatility Transmission"
      subtitle="Configure ETF holdings ingestion (EDGAR N-PORT), measurement & residualization parameters, position sizing derates, and portfolio covariance adjustments."
      backTo="/settings"
      fetchSettings={() => api.getEtfTransmissionSettings()}
      updateSettings={(values) => api.updateEtfTransmissionSettings(values)}
    />
  );
}
