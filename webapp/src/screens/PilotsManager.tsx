import { ScanConfigCard } from "../components/Pilots/ScanConfigCard";
import { ExecutionQueue } from "../components/Pilots/ExecutionQueue";
import { PerformanceMetricsCard } from "../components/Pilots/PerformanceMetricsCard";

export function PilotsManager() {
  return (
    <div className="page-container">
      <header className="page-header">
        <h1 className="page-title">Pilots Manager</h1>
      </header>
      <div className="page-content" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        <ScanConfigCard />
        <PerformanceMetricsCard />
        <ExecutionQueue />
      </div>
    </div>
  );
}
