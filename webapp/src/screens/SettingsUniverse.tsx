import { UniverseManager } from "../components/UniverseManager";
import { UniverseCoverage } from "../components/UniverseCoverage";
import { SectionCard } from "../components/SectionCard";
import { TabGuide } from "../components/TabGuide";

export function SettingsUniverse() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Tracked Universe</h2>
        <p style={{ color: "var(--text-secondary)", margin: 0, fontSize: "var(--t-body)" }}>
          Manage your configured DEFAULT_TICKERS list.
        </p>
      </div>

      <TabGuide tabKey="settings-universe" />

      <SectionCard
        title="Tracked universe"
        sub="Add or remove any stock. This list is a FALLBACK only — the pipeline uses it each cycle only when your watchlist and scan-discovery are both empty; a warning below tells you when that's not the case. Raw data for any symbol is explorable immediately in Data Explorer."
      >
        <UniverseManager />
        <UniverseCoverage />
      </SectionCard>
    </div>
  );
}
