import { useState } from "react";
import { api } from "../api/client";
import { useMutation } from "../hooks/useMutation";
import { Button, Input } from "./ui";
import { Modal } from "./Modal";
import { Toggle } from "./Toggle";
import { theme } from "../theme";

/**
 * KillSwitchToggle — the single, shared pause/resume control for the ONE
 * global kill switch (execution/kill_switch.py), via POST /automation/{pause,
 * resume} (api.pauseAutomation/resumeAutomation). Before this component there
 * were two near-identical ~70-line copies — Settings' "Signal generation"
 * section and the Agentic Trading tab's "Controls" section — that had already
 * drifted (different labels, a slightly different `disabled` guard) even
 * though they drive the exact same switch. That drift was the whole point of
 * UX backlog finding #6: one switch, two names, two code copies.
 *
 * Both directions require a typed reason via a confirm Modal. That reason gate
 * is a fat-finger guard, NOT a security control — the real gates are all
 * server-side (the command token, AUTOMATION_WRITES_ENABLED, and the
 * ADVISORY_ONLY check on resume). When `advisoryOnly` is false the Toggle is
 * disabled from the paused state: resume must happen at the console while live
 * order submission is enabled (`resumeBlocked`).
 *
 * `noun` parameterizes the operator-facing copy ("Signal generation") so the
 * two screens read as the same control. `disabled` lets a caller force the
 * control off before it knows the switch state (the Agentic tab renders this
 * before its status has loaded). `showReason` renders the inline "Reason: ..."
 * line — Settings shows it here; the Agentic tab surfaces the same reason in
 * its own status header instead, so it leaves this off to avoid a duplicate.
 */
export function KillSwitchToggle({
  noun,
  active,
  reason,
  advisoryOnly,
  onChanged,
  disabled = false,
  showReason = false,
}: {
  noun: string; // operator-facing label noun, e.g. "Signal generation"
  active: boolean; // kill switch active == paused
  reason: string | null;
  advisoryOnly: boolean;
  onChanged: () => void;
  disabled?: boolean; // external disable (e.g. status not loaded yet)
  showReason?: boolean; // render the inline "Reason: ..." line here
}) {
  const [confirmKind, setConfirmKind] = useState<"pause" | "resume" | null>(null);
  const [inputReason, setInputReason] = useState("");
  const pauseMutation = useMutation((r: string) => api.pauseAutomation(r));
  const resumeMutation = useMutation((r: string) => api.resumeAutomation(r));

  const running = !active;
  const busy = pauseMutation.pending || resumeMutation.pending;
  const resumeBlocked = !running && !advisoryOnly;
  const nounLower = noun.toLowerCase();

  const openConfirm = (next: boolean) => {
    setInputReason("");
    setConfirmKind(next ? "resume" : "pause");
  };

  const confirmAction = async () => {
    if (confirmKind === "pause") {
      await pauseMutation.run(inputReason);
    } else if (confirmKind === "resume") {
      await resumeMutation.run(inputReason);
    }
    setConfirmKind(null);
    onChanged();
  };

  return (
    <>
      <Toggle
        checked={running}
        onChange={openConfirm}
        label={running ? `${noun}: Running` : `${noun}: Paused`}
        disabled={disabled || resumeBlocked}
        pending={busy}
      />
      {showReason && active && reason && (
        <p style={{ color: theme.textMuted, fontSize: 12, marginTop: 8 }}>Reason: {reason}</p>
      )}
      {resumeBlocked && (
        <p style={{ color: theme.caution, fontSize: 12, marginTop: 8 }}>
          Resume must be done at the console while live trading is enabled.
        </p>
      )}
      <p
        style={{
          color: theme.textMuted,
          fontSize: "var(--t-caption)",
          marginTop: 8,
          lineHeight: 1.45,
        }}
      >
        Pausing does not stop the schedule — cycles still run, they just
        produce no recommendations (or submit no orders in live mode).
      </p>
      {(pauseMutation.error || resumeMutation.error) && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>{pauseMutation.error ?? resumeMutation.error}</span>
        </div>
      )}

      {confirmKind && (
        <Modal
          ariaLabel={confirmKind === "pause" ? `Pause ${nounLower}` : `Resume ${nounLower}`}
          onClose={() => setConfirmKind(null)}
        >
          <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>
            {confirmKind === "pause" ? `Pause ${nounLower}?` : `Resume ${nounLower}?`}
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
            {confirmKind === "pause"
              ? "New recommendations stop until resumed. The schedule keeps running."
              : "Recommendations resume on the next scheduled or manual run."}
          </p>
          <Input
            label="Reason"
            value={inputReason}
            onChange={(e) => setInputReason(e.target.value)}
            hint="Required."
          />
          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
            <Button variant="neutral" onClick={() => setConfirmKind(null)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={confirmAction}
              disabled={!inputReason.trim()}
              pending={busy}
              style={{ flex: 2 }}
            >
              {confirmKind === "pause" ? "Pause" : "Resume"}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
