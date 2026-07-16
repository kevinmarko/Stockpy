import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AutomationSchedule, AutomationStatus } from "../api/types";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { EmptyState, ErrorState, Loading, MetricBadge } from "../components/ui";
import { Modal } from "../components/Modal";
import { Button } from "../components/ui";
import { PwaStatusSection } from "../components/PwaStatusSection";
import { fmtAge, fmtDate } from "../format";
import { theme } from "../theme";
import { resetOnboarding } from "../onboarding";

/**
 * Data & Automation settings — Phase 2 (read-only): "did the pipeline run,
 * and when" plus a read-only view of the schedule, replacing an operator's
 * SSH + journalctl loop for that one question. Manual Run Now and
 * pause/resume/interval writes are a later phase — nothing on this screen
 * mutates backend state (Reset onboarding is the one exception, and it's
 * client-side localStorage only, never a network write).
 */
export function Settings() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const {
    data: status,
    loading: statusLoading,
    error: statusError,
    status: statusHttpStatus,
    reload: reloadStatus,
  } = useApi<AutomationStatus>(() => api.getAutomationStatus(), []);

  const {
    data: schedule,
    loading: scheduleLoading,
    error: scheduleError,
    status: scheduleHttpStatus,
    reload: reloadSchedule,
  } = useApi<AutomationSchedule>(() => api.getAutomationSchedule(), []);

  // Poll every 3s ONLY while a run is actually in flight -- not a phone's
  // radio budget spent polling a status that changes once every 5 minutes.
  const isRunInFlight = Boolean(
    status?.daemon.is_running || status?.progress?.state === "running"
  );
  usePoll(reloadStatus, 3000, isRunInFlight);

  return (
    <div className="screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: 14,
          marginBottom: 8,
        }}
      >
        ← Back
      </button>
      <h1 className="screen-title">Data &amp; Automation</h1>
      <p className="screen-sub">
        Pipeline run status and the automated schedule, without SSHing into
        the host.
      </p>

      <PipelineStatusSection
        status={status}
        loading={statusLoading}
        error={statusError}
        httpStatus={statusHttpStatus}
        onRetry={reloadStatus}
      />

      <ScheduleSection
        schedule={schedule}
        loading={scheduleLoading}
        error={scheduleError}
        httpStatus={scheduleHttpStatus}
        onRetry={reloadSchedule}
      />

      <div style={{ marginTop: 16 }}>
        <PwaStatusSection />
      </div>

      <ResetOnboardingSection />

      <p
        style={{
          color: theme.textMuted,
          fontSize: "var(--t-footnote)",
          marginTop: 20,
          textAlign: "center",
          lineHeight: 1.5,
        }}
      >
        Run status is composed from multiple sources, each labeled with where
        it came from — nothing here is fabricated when a signal is
        unavailable.
      </p>
    </div>
  );
}

function SectionCard({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children: ReactNode;
}) {
  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && (
        <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
          {sub}
        </p>
      )}
      {children}
    </section>
  );
}

function PipelineStatusSection({
  status,
  loading,
  error,
  httpStatus,
  onRetry,
}: {
  status: AutomationStatus | null;
  loading: boolean;
  error: string | null;
  httpStatus: number | null;
  onRetry: () => void;
}) {
  return (
    <SectionCard title="Pipeline status">
      {loading && <Loading lines={3} />}
      {!loading && error && (
        <ErrorState message={error} status={httpStatus} onRetry={onRetry} />
      )}
      {!loading && !error && status && (
        <div className="list">
          <div className="row">
            <span className="row-title">Daemon</span>
            <MetricBadge
              label={status.daemon.alive ? "Alive" : "Not reachable"}
              value={
                status.daemon.source === "none"
                  ? "no signal"
                  : status.daemon.source === "daemon_json"
                    ? "last known state"
                    : "live"
              }
              good={status.daemon.alive}
            />
          </div>

          <div className="row">
            <span className="row-title">Last run</span>
            {status.last_run ? (
              <MetricBadge
                label={status.last_run.state}
                value={fmtDate(status.last_run.finished_at ?? status.last_run.started_at)}
                good={
                  status.last_run.state === "succeeded"
                    ? true
                    : status.last_run.state === "failed"
                      ? false
                      : null
                }
              />
            ) : (
              <span style={{ color: theme.textMuted, fontSize: 13 }}>—</span>
            )}
          </div>

          {status.last_run_source === "state_snapshot" && (
            <div className="notice notice-info" style={{ marginTop: 10 }}>
              <span>ℹ️</span>
              <span>
                No run record — the daemon has never triggered a run this
                process lifetime (or restarted since). Last pipeline output:{" "}
                {fmtAge(status.pipeline.snapshot_age_seconds)}.
              </span>
            </div>
          )}

          <div className="row">
            <span className="row-title">Last pipeline output</span>
            <span style={{ color: theme.textSecondary, fontSize: 13 }}>
              {fmtAge(status.pipeline.snapshot_age_seconds)}
              {status.pipeline.snapshot_age_source === "mtime" && " (file time)"}
            </span>
          </div>

          {status.progress && !status.progress.is_terminal && !status.progress.stale && (
            <div className="row">
              <span className="row-title">In progress</span>
              <span style={{ color: theme.accent, fontSize: 13 }}>
                {status.progress.stage} ({status.progress.stage_index + 1}/
                {status.progress.stage_total}) · {status.progress.percent.toFixed(0)}%
              </span>
            </div>
          )}

          {status.kill_switch.active && (
            <div className="notice notice-warn" style={{ marginTop: 10 }}>
              <span>⚠️</span>
              <span>
                Kill switch active{status.kill_switch.reason ? `: ${status.kill_switch.reason}` : ""}.
              </span>
            </div>
          )}

          <ErrorsSubsection errors={status.errors} />

          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              marginTop: 12,
              lineHeight: 1.45,
            }}
          >
            {status.pipeline.heartbeat_age_seconds == null
              ? status.pipeline.heartbeat_note
              : `Heartbeat: ${fmtAge(status.pipeline.heartbeat_age_seconds)}.`}
          </p>
        </div>
      )}
    </SectionCard>
  );
}

function ErrorsSubsection({ errors }: { errors: AutomationStatus["errors"] }) {
  if (errors.entry_count === 0) {
    return (
      <div style={{ marginTop: 12 }}>
        <div className="row-sub" style={{ marginBottom: 4 }}>
          Errors
        </div>
        <EmptyState title="No errors" hint="The last run completed cleanly." />
      </div>
    );
  }
  return (
    <div style={{ marginTop: 12 }}>
      <div className="row-sub" style={{ marginBottom: 4 }}>
        Errors ({errors.entry_count})
      </div>
      <div className="notice notice-warn">
        <span>⚠️</span>
        <span>
          {errors.entry_count} symbol{errors.entry_count === 1 ? "" : "s"} failed on
          the last run{errors.entries.length < errors.entry_count
            ? ` (showing ${errors.entries.length})`
            : ""}
          .
        </span>
      </div>
      <div className="list" style={{ marginTop: 4 }}>
        {errors.entries.map((entry, i) => (
          <div className="row" key={i} style={{ padding: "6px 0" }}>
            <span className="row-sub">{JSON.stringify(entry)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ScheduleSection({
  schedule,
  loading,
  error,
  httpStatus,
  onRetry,
}: {
  schedule: AutomationSchedule | null;
  loading: boolean;
  error: string | null;
  httpStatus: number | null;
  onRetry: () => void;
}) {
  return (
    <SectionCard title="Schedule" sub="Read-only in this build.">
      {loading && <Loading lines={2} />}
      {!loading && error && (
        <ErrorState message={error} status={httpStatus} onRetry={onRetry} />
      )}
      {!loading && !error && schedule && (
        <>
          <div className="list">
            <div className="row">
              <span className="row-title">Interval</span>
              <span style={{ color: theme.textSecondary, fontSize: 13 }}>
                {schedule.interval.running_value == null
                  ? "unknown"
                  : `${schedule.interval.running_value}s`}
              </span>
            </div>
          </div>
          {schedule.interval.drift && (
            <div className="notice notice-info" style={{ marginTop: 10 }}>
              <span>ℹ️</span>
              <span>
                Running: {schedule.interval.running_value}s · Configured:{" "}
                {schedule.interval.configured_value}s. Restart the daemon to
                apply the configured value.
              </span>
            </div>
          )}

          <div style={{ marginTop: 14 }}>
            <div className="row-sub" style={{ marginBottom: 6 }}>
              Cron ({schedule.cron.source})
            </div>
            <div className="list">
              {schedule.cron.entries.map((entry, i) => (
                <div className="row" key={i} style={{ alignItems: "flex-start" }}>
                  <div className="row-main">
                    <span className="row-title" style={{ fontFamily: "monospace", fontSize: 13 }}>
                      {entry.schedule}
                    </span>
                    {entry.comment && <span className="row-sub">{entry.comment}</span>}
                  </div>
                </div>
              ))}
            </div>
            <p
              style={{
                color: theme.textMuted,
                fontSize: "var(--t-caption)",
                marginTop: 8,
                lineHeight: 1.45,
              }}
            >
              {schedule.cron.note}
            </p>
          </div>
        </>
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
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
        Clears the local "onboarding complete" marker and returns to the
        Choose Pilot step. Does not touch any account, follow, or backend
        state — this is a local device setting only.
      </p>
      <Button variant="neutral" onClick={() => setConfirming(true)}>
        Reset onboarding
      </Button>

      {confirming && (
        <Modal ariaLabel="Reset onboarding" onClose={() => setConfirming(false)}>
          <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>Reset onboarding?</h2>
          <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0 }}>
            You'll be taken back to the Choose Pilot step. This only affects
            this device.
          </p>
          <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
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
