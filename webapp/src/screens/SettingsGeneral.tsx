import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { AutomationStatus } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, Loading, ErrorState } from "../components/ui";
import { KillSwitchToggle } from "../components/KillSwitchToggle";
import { Modal } from "../components/Modal";
import { PwaStatusSection } from "../components/PwaStatusSection";
import { theme } from "../theme";
import { resetOnboarding } from "../onboarding";
import { SectionCard } from "../components/SectionCard";

export function SettingsGeneral() {
  const {
    data: status,
    loading: statusLoading,
    error: statusError,
    status: statusHttpStatus,
    reload: reloadStatus,
  } = useApi<AutomationStatus>(() => api.getAutomationStatus(), []);

  if (statusLoading) return <Loading />;
  if (statusError) return <ErrorState message={statusError} status={statusHttpStatus} onRetry={reloadStatus} />;
  if (!status) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>General & Execution Mode</h2>
        <p style={{ color: theme.textSecondary, margin: 0, fontSize: "var(--t-body)" }}>
          Control global orchestrator behavior, including the kill switch and live trading authorization.
        </p>
      </div>

      <SignalGenerationSection
        active={status.kill_switch.active}
        reason={status.kill_switch.reason}
        advisoryOnly={status.advisory_only}
        onChanged={reloadStatus}
      />

      <ExecutionModeSection
        advisoryOnly={status.advisory_only}
        dryRun={status.dry_run}
        alpacaPaper={status.alpaca_paper}
        onChanged={reloadStatus}
      />

      <PwaStatusSection />
      
      <ResetOnboardingSection />
    </div>
  );
}

function SignalGenerationSection({
  active,
  reason,
  advisoryOnly,
  onChanged,
}: {
  active: boolean; // kill switch active == paused
  reason: string | null;
  advisoryOnly: boolean;
  onChanged: () => void;
}) {
  return (
    <SectionCard title="Signal generation">
      <KillSwitchToggle
        noun="Signal generation"
        active={active}
        reason={reason}
        advisoryOnly={advisoryOnly}
        onChanged={onChanged}
        showReason
      />
    </SectionCard>
  );
}

function ExecutionModeSection({
  advisoryOnly,
  dryRun,
  alpacaPaper,
  onChanged,
}: {
  advisoryOnly: boolean;
  dryRun: boolean;
  alpacaPaper: boolean;
  onChanged: () => void;
}) {
  const [selectedMode, setSelectedMode] = useState<"advisory" | "simulation" | "paper" | "live" | null>(null);
  
  const currentMode = advisoryOnly
    ? "advisory"
    : dryRun
    ? "simulation"
    : alpacaPaper
    ? "paper"
    : "live";

  const modeMutation = useMutation((mode: "advisory" | "simulation" | "paper" | "live") => 
    api.setExecutionMode({
      mode: mode,
      advisory_only: mode === "advisory"
    })
  );

  const confirmChange = async () => {
    if (!selectedMode) return;
    await modeMutation.run(selectedMode);
    setSelectedMode(null);
    onChanged();
  };

  return (
    <SectionCard title="Execution Mode">
      <div style={{ marginBottom: "var(--s-3)", color: "var(--text-muted)" }}>
        Controls whether the orchestrator is permitted to place live trades or is quarantined.
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2-5)" }}>
        <Button
          variant={currentMode === "advisory" ? "primary" : "neutral"}
          onClick={() => setSelectedMode("advisory")}
          disabled={currentMode === "advisory"}
        >
          🛑 Advisory Only
        </Button>
        <Button
          variant={currentMode === "simulation" ? "primary" : "neutral"}
          onClick={() => setSelectedMode("simulation")}
          disabled={currentMode === "simulation"}
        >
          🧪 Simulation
        </Button>
        <Button
          variant={currentMode === "paper" ? "primary" : "neutral"}
          onClick={() => setSelectedMode("paper")}
          disabled={currentMode === "paper"}
        >
          📝 Paper Trading
        </Button>
        <Button
          variant={currentMode === "live" ? "primary" : "neutral"}
          style={currentMode === "live" ? { backgroundColor: "var(--decline)" } : {}}
          onClick={() => setSelectedMode("live")}
          disabled={currentMode === "live"}
        >
          🔴 Live Production
        </Button>
      </div>
      
      {selectedMode && (
        <Modal ariaLabel="Confirm Mode Change" onClose={() => setSelectedMode(null)}>
          <div style={{ marginBottom: "var(--s-4)" }}>
            <h3 style={{ margin: "0 0 var(--s-4) 0" }}>Confirm Mode Change</h3>
            You are changing the execution mode from <strong>{currentMode}</strong> to <strong>{selectedMode}</strong>.
            <br/><br/>
            {selectedMode === "live" && <strong style={{ color: "var(--decline)" }}>WARNING: This will allow the engine to execute real trades with real money.</strong>}
          </div>
          <div style={{ display: "flex", gap: "var(--s-2-5)" }}>
            <Button variant="neutral" onClick={() => setSelectedMode(null)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              style={selectedMode === "live" ? { backgroundColor: "var(--decline)", flex: 2 } : { flex: 2 }}
              onClick={confirmChange}
              pending={modeMutation.pending}
            >
              Confirm Change
            </Button>
          </div>
        </Modal>
      )}
    </SectionCard>
  );
}

function ResetOnboardingSection() {
  const nav = useNavigate();
  const [confirming, setConfirming] = useState(false);

  const doReset = () => {
    resetOnboarding();
    setConfirming(false);
    nav("/");
  };

  return (
    <SectionCard title="Reset onboarding">
      <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-3)" }}>
        Clears the local "onboarding complete" marker and returns to the
        Choose Pilot step. Does not touch any account, follow, or backend
        state — this is a local device setting only.
      </p>
      <Button variant="neutral" onClick={() => setConfirming(true)}>
        Reset onboarding
      </Button>

      {confirming && (
        <Modal ariaLabel="Reset onboarding" onClose={() => setConfirming(false)}>
          <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>Reset onboarding?</h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
            You'll be taken back to the Choose Pilot step. This only affects
            this device.
          </p>
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button variant="neutral" onClick={() => setConfirming(false)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button variant="primary" onClick={doReset} style={{ flex: 2 }}>
              Reset
            </Button>
          </div>
        </Modal>
      )}
    </SectionCard>
  );
}
