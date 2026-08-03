import { UniverseManager } from "../components/UniverseManager";
import { UniverseCoverage } from "../components/UniverseCoverage";
import { SectionCard } from "../components/SectionCard";

export function SettingsUniverse() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Tracked Universe</h2>
        <p style={{ color: "var(--text-secondary)", margin: 0, fontSize: "var(--t-body)" }}>
          Manage the active symbols that the pipeline processes on each run.
        </p>
      </div>

      <SectionCard
        title="Tracked universe"
        sub="Add or remove any stock. Changes take effect on the next pipeline run — raw data for any symbol is explorable immediately in Data Explorer."
      >
        <UniverseManager />
        <UniverseCoverage />
      </SectionCard>
    </div>
  );
}
