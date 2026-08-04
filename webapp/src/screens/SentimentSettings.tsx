import { api } from "../api/client";
import { GenericSettingsEditor } from "../components/GenericSettingsEditor";

export function SentimentSettings() {
  return (
    <GenericSettingsEditor
      title="Sentiment & News Ingestion"
      subtitle="Configure ingestion pipelines, sources (StockTwits, Reddit, Google News, EDGAR, GDELT), FinBERT scoring, and attention proxies."
      backTo="/settings"
      fetchSettings={() => api.getSentimentSettings()}
      updateSettings={(values, confirm) => api.updateSentimentSettings(values, confirm)}
    />
  );
}
