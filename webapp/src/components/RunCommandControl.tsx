import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { api } from "../api/client";
import { usePoll } from "../hooks/usePoll";
import type { CommandSpec, CommandJobParams, JobRecord } from "../api/types";
import { highStakesReason, DISALLOWED_EXECUTE_COMMANDS } from "../commandParse";
import { Button } from "./ui";
import { Modal } from "./Modal";
import { LogStream } from "./LogStream";
import { RecentRunsLog } from "./RecentRunsLog";
import { theme } from "../theme";

const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);

/**
 * RunCommandControl — the "Run" button plus its full job lifecycle (high-stakes
 * confirm, toast, live job-status line, streaming log, cancel, recent runs).
 *
 * Extracted out of Commands.tsx's free-text CommandBar so CommandFormBuilder's
 * "Execute Command" action can reuse the exact same tested logic instead of a
 * separate, easily-forgotten no-op stub — see docs/known_issues (or the PR that
 * introduced this file) for the bug this fixes: the two entry points had drifted
 * out of sync, with the form-builder path silently doing nothing.
 */
export function RunCommandControl({
  command,
  subcommand,
  argTokens,
  disabled,
  composed,
  resetKey,
}: {
  command: CommandSpec | null;
  subcommand: CommandSpec | null;
  argTokens: string[];
  disabled: boolean;
  composed: string;
  resetKey: unknown;
}) {
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [recentJobs, setRecentJobs] = useState<JobRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState(false);

  useEffect(() => {
    setActiveJob(null);
    setError(null);
    setPendingConfirm(false);
  }, [resetKey]);

  usePoll(
    async () => {
      if (!activeJob) return;
      try {
        const updated = await api.getJobStatus(activeJob.job_id);
        setActiveJob(updated);
        setRecentJobs((prev) => {
          const idx = prev.findIndex((j) => j.job_id === updated.job_id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = updated;
            return next;
          }
          return [updated, ...prev];
        });
      } catch {
        // ignore transient poll failure
      }
    },
    1500,
    Boolean(activeJob) && !TERMINAL_STATUSES.has(activeJob?.status ?? "")
  );

  const runCommand = async () => {
    // The compact "Job {id} — {status}" line below (plus LogStream/
    // RecentRunsLog) is a persistent panel, but it only helps an operator
    // who's still looking at this exact spot on the screen -- mirrors
    // Console.tsx's own QUICK_ACTIONS launcher, which toasts launch
    // success/failure in addition to its own always-visible job table for
    // the same reason.
    const label = subcommand ? `${command!.name} ${subcommand.name}` : command!.name;
    try {
      const params: CommandJobParams = {
        command: command!.name,
        subcommand: subcommand?.name ?? null,
        args: argTokens,
        confirm: true,
      };
      const job = await api.createJob("command", { ...params });
      setActiveJob(job);
      setRecentJobs((prev) => [job, ...prev]);
      setError(null);
      toast.success(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Command launched</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {label} — job {job.job_id}
          </span>
        </div>
      );
    } catch (err: any) {
      const message = err?.message ?? String(err);
      setError(message);
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>{label} failed to launch</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {message}
          </span>
        </div>
      );
    }
  };

  const handleRunClick = () => {
    const reason = highStakesReason(command, argTokens);
    if (reason) {
      setPendingConfirm(true);
    } else {
      void runCommand();
    }
  };

  const handleCancel = async () => {
    if (!activeJob) return;
    try {
      const res = await api.cancelJob(activeJob.job_id);
      if (res.cancelled) {
        const updated = await api.getJobStatus(activeJob.job_id);
        setActiveJob(updated);
      } else {
        setError("Cancel was requested but could not be confirmed — the job may still be running.");
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    }
  };

  if (!command) return null;

  if (DISALLOWED_EXECUTE_COMMANDS.has(command.name)) {
    return (
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }} data-testid="command-run-disallowed">
        {command.name} opens a native desktop window on the server — copy the command above and run it locally.
      </div>
    );
  }

  const reason = highStakesReason(command, argTokens);

  return (
    <div>
      <Button onClick={handleRunClick} disabled={disabled} data-testid="command-run-button">
        Run
      </Button>

      {error && (
        <div style={{ color: theme.decline, fontSize: "var(--t-body)", marginTop: "var(--s-2)" }} data-testid="command-run-error">
          {error}
        </div>
      )}

      {activeJob && (
        <div style={{ marginTop: "var(--s-3)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)" }}>
            <span style={{ color: theme.textSecondary, fontSize: "var(--t-caption)" }} data-testid="command-run-status">
              Job {activeJob.job_id} — {activeJob.status}
            </span>
            {activeJob.cancellable && activeJob.is_running !== false && (
              <Button variant="neutral" onClick={handleCancel} data-testid="command-run-cancel">
                Cancel
              </Button>
            )}
          </div>
          <div style={{ marginTop: "var(--s-2-5)" }}>
            <LogStream jobId={activeJob.job_id} isStreaming={Boolean(activeJob)} />
          </div>
        </div>
      )}

      {recentJobs.length > 0 && (
        <div style={{ marginTop: "var(--s-5)" }}>
          <RecentRunsLog jobs={recentJobs} />
        </div>
      )}

      {pendingConfirm && (
        <Modal ariaLabel="Confirm command" onClose={() => setPendingConfirm(false)}>
          <div data-testid="command-confirm">
            <div className="tile-label" style={{ marginBottom: "var(--s-2)" }}>
              Confirm command
            </div>
            <p style={{ color: theme.textSecondary, marginTop: 0 }}>{reason}</p>
            <code
              style={{
                display: "block",
                padding: "var(--s-2-5) var(--s-3)",
                background: theme.surface,
                border: `1px solid ${theme.border}`,
                borderRadius: "var(--r-sm)",
                fontFamily: "var(--font-mono, ui-monospace, monospace)",
                color: theme.textPrimary,
                overflowX: "auto",
                whiteSpace: "pre",
              }}
            >
              {composed}
            </code>
            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}>
              <Button
                variant="neutral"
                onClick={() => setPendingConfirm(false)}
                data-testid="command-confirm-cancel"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setPendingConfirm(false);
                  void runCommand();
                }}
                data-testid="command-confirm-yes"
              >
                Yes, run it
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
