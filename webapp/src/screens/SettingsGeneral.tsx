import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { AutomationStatus } from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import { Button, Input, Loading, ErrorState, Notice } from "../components/ui";
import { KillSwitchToggle } from "../components/KillSwitchToggle";
import { Modal } from "../components/Modal";
import { PwaStatusSection } from "../components/PwaStatusSection";
import { theme } from "../theme";
import { resetOnboarding } from "../onboarding";
import { SectionCard } from "../components/SectionCard";
import { TabGuide } from "../components/TabGuide";
import { buildConfirmMap } from "../settingsLiveness";

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

      <TabGuide tabKey="settings-general" />

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

/**
 * Every mode change writes ADVISORY_ONLY -- "the single highest-consequence
 * write in the whole settings surface" (settings_keysets.py) -- and any mode
 * other than "advisory" also writes DRY_RUN. Both are
 * `settings_keysets.DANGEROUS_KEYS` fields (ALPACA_PAPER, also written for
 * non-advisory modes, is NOT -- it's Alpaca's own paper/live account
 * selector, not a broker-agnostic quarantine, and deliberately left
 * unhardened here), and the backend now rejects this write (422, nothing
 * written) unless every one of them is echoed back in `confirm` -- the SAME
 * contract `PUT /settings/tunables` enforces for ADVISORY_ONLY/DRY_RUN via
 * its own `DangerousConfirmDialog` (GenericSettingsEditor.tsx). This helper
 * is the single place that decides which keys a given target mode touches,
 * so the typed-confirmation gate below and the request body it builds can
 * never drift apart.
 */
function dangerousKeysFor(mode: "advisory" | "simulation" | "paper" | "live"): string[] {
  return mode === "advisory" ? ["ADVISORY_ONLY"] : ["ADVISORY_ONLY", "DRY_RUN"];
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
  const [typed, setTyped] = useState<Record<string, string>>({});

  const currentMode = advisoryOnly
    ? "advisory"
    : dryRun
    ? "simulation"
    : alpacaPaper
    ? "paper"
    : "live";

  const modeMutation = useMutation(
    (mode: "advisory" | "simulation" | "paper" | "live") =>
      api.setExecutionMode({
        mode: mode,
        advisory_only: mode === "advisory",
        confirm: buildConfirmMap(dangerousKeysFor(mode)),
      }),
    { successMessage: (result) => `Execution mode changed to ${result.mode}` }
  );

  const pendingDangerous = selectedMode ? dangerousKeysFor(selectedMode) : [];
  // Typing the name of EVERY dangerous field this mode change touches
  // mirrors GenericSettingsEditor.tsx's DangerousConfirmDialog exactly (the
  // same per-key echo pattern, not one blanket keyword) -- an AFFORDANCE, not
  // the enforcement: the server rejects the write regardless of what the
  // client sends if `confirm` doesn't echo every dangerous key correctly
  // (see _require_dangerous_confirmation).
  const allConfirmed = pendingDangerous.every((k) => (typed[k] ?? "").trim() === k);

  const openConfirm = (mode: "advisory" | "simulation" | "paper" | "live") => {
    setTyped({});
    modeMutation.reset();
    setSelectedMode(mode);
  };

  const closeConfirm = () => {
    setSelectedMode(null);
    setTyped({});
  };

  const confirmChange = async () => {
    if (!selectedMode || !allConfirmed) return;
    const res = await modeMutation.run(selectedMode);
    // Only close/reload on an actual success -- a failed write (e.g. a
    // server-side confirmation rejection, AUTOMATION_WRITES_ENABLED off)
    // must leave the dialog open with its error visible, not silently
    // vanish as if it had applied.
    if (res) {
      closeConfirm();
      onChanged();
    }
  };

  return (
    <SectionCard title="Execution Mode">
      <div style={{ marginBottom: "var(--s-3)", color: "var(--text-muted)" }}>
        Controls whether the orchestrator is permitted to place live trades or is quarantined.
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2-5)" }}>
        <Button
          variant={currentMode === "advisory" ? "primary" : "neutral"}
          onClick={() => openConfirm("advisory")}
          disabled={currentMode === "advisory"}
        >
          🛑 Advisory Only
        </Button>
        <Button
          variant={currentMode === "simulation" ? "primary" : "neutral"}
          onClick={() => openConfirm("simulation")}
          disabled={currentMode === "simulation"}
        >
          🧪 Simulation
        </Button>
        <Button
          variant={currentMode === "paper" ? "primary" : "neutral"}
          onClick={() => openConfirm("paper")}
          disabled={currentMode === "paper"}
        >
          📝 Paper Trading
        </Button>
        <Button
          variant={currentMode === "live" ? "primary" : "neutral"}
          style={currentMode === "live" ? { backgroundColor: "var(--decline)" } : {}}
          onClick={() => openConfirm("live")}
          disabled={currentMode === "live"}
        >
          🔴 Live Production
        </Button>
      </div>

      {selectedMode && (
        <Modal ariaLabel="Confirm Mode Change" onClose={closeConfirm}>
          <div style={{ marginBottom: "var(--s-4)" }}>
            <h3 style={{ margin: "0 0 var(--s-4) 0" }}>Confirm Mode Change</h3>
            You are changing the execution mode from <strong>{currentMode}</strong> to <strong>{selectedMode}</strong>.
            <br/><br/>
            {selectedMode === "live" && <strong style={{ color: "var(--decline)" }}>WARNING: This will allow the engine to execute real trades with real money.</strong>}
          </div>

          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
            This touches this platform&apos;s safety and execution controls. Confirm
            each field by typing its name exactly.
          </p>
          {pendingDangerous.map((k) => (
            <div key={k} style={{ marginTop: "var(--s-2-5)" }}>
              <Input
                label={`Type "${k}" to confirm`}
                value={typed[k] ?? ""}
                onChange={(e) => setTyped((s) => ({ ...s, [k]: e.target.value }))}
                hint="Required."
              />
            </div>
          ))}

          {modeMutation.error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }} data-testid="execution-mode-error">
              <span>⚠️</span>
              <span>{modeMutation.error}</span>
            </Notice>
          )}
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button variant="neutral" onClick={closeConfirm} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              style={selectedMode === "live" ? { backgroundColor: "var(--decline)", flex: 2 } : { flex: 2 }}
              onClick={confirmChange}
              disabled={!allConfirmed}
              pending={modeMutation.pending}
              data-testid="execution-mode-confirm"
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
