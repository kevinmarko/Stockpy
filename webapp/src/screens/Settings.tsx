import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type {
  AutomationSchedule,
  AutomationStatus,
  Follow,
  LlmCapabilityRow,
  LlmStatus,
  StrategyMatrix,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { useMutation } from "../hooks/useMutation";
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  Loading,
  MetricBadge,
  StaleDataNotice,
} from "../components/ui";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
import { PwaStatusSection } from "../components/PwaStatusSection";
import { fmtAge, fmtDate, fmtUsd, timeAgo } from "../format";
import { theme } from "../theme";
import { resetOnboarding } from "../onboarding";

/**
 * Data & Automation settings — "did the pipeline run, and when", a manual
 * Run Now trigger, pause/resume of signal generation, a read-only-by-default
 * schedule view with an opt-in interval write, and per-pilot re-plan —
 * replacing an operator's SSH + journalctl loop for all of it. Every write
 * on this screen (run/pause/resume/interval) fails closed server-side when
 * its gate isn't configured (FOLLOW_API_TOKEN / AUTOMATION_WRITES_ENABLED /
 * ADVISORY_ONLY) — the UI here renders whatever the server actually allowed,
 * never assumes a write succeeded.
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

      {status && (
        <SignalGenerationSection
          active={status.kill_switch.active}
          reason={status.kill_switch.reason}
          advisoryOnly={status.advisory_only}
          onChanged={reloadStatus}
        />
      )}

      <SignalModulesLink />

      <ActiveFollowsSection />

      <LlmStatusSection />

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

/** Badge label per capability status (the Streamlit STATUS_BADGE analogue). */
const LLM_BADGE_LABEL: Record<LlmCapabilityRow["status"], string> = {
  ready: "Ready",
  disabled: "Off",
  missing_key: "Key missing",
  invalid_key: "Key rejected",
  not_built: "Not built",
};

/**
 * AI provider status — presence + LAST-REAL-CALL telemetry over GET /llm/status.
 * The platform never probes a provider to test a key, so a null verdict means
 * "no call has been made with the current key yet" (the expected state with LLM
 * commentary off by default), NOT "broken". All copy is past-tense + timestamped.
 * No usePoll: config changes on an operator's .env edit, not on a timer.
 */
function LlmStatusSection() {
  const { data, loading, error, status, stale, cachedAt, reload } = useApi<LlmStatus>(
    () => api.getLlmStatus(),
    []
  );

  return (
    <SectionCard
      title="AI providers"
      sub="Which LLM capabilities are configured, and what happened on the last real call."
    >
      {loading && <Loading lines={3} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        <div className="list">
          {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}
          {data.capabilities.map((c) => {
            const tel = c.active_provider ? data.providers[c.active_provider] : null;
            // disabled / not_built are a deliberate "off" -> neutral, never a warning.
            const good =
              c.status === "ready"
                ? true
                : c.status === "invalid_key" || c.status === "missing_key"
                  ? false
                  : null;
            return (
              <div key={c.key} style={{ marginBottom: 6 }}>
                <div className="row">
                  <span className="row-title">{c.label}</span>
                  <MetricBadge
                    label={LLM_BADGE_LABEL[c.status]}
                    value={c.active_provider ?? c.provider_keys.join(", ")}
                    good={good}
                  />
                </div>
                {c.status === "invalid_key" && c.invalid_provider && (
                  <div className="notice notice-warn" style={{ marginTop: 8 }}>
                    <span aria-hidden>⚠️</span>
                    <span>
                      The last real {c.invalid_provider} call
                      {tel?.checked_at ? ` (${timeAgo(tel.checked_at)})` : ""} was rejected as
                      unauthenticated. Check <code>{c.provider_keys.join(", ")}</code> in{" "}
                      <code>.env</code>. This clears automatically on the next successful call, or
                      as soon as the key is changed.
                    </span>
                  </div>
                )}
                {c.status === "missing_key" && (
                  <div className="notice notice-warn" style={{ marginTop: 8 }}>
                    <span aria-hidden>⚠️</span>
                    <span>
                      Enabled, but <code>{c.provider_keys.join(", ")}</code> is unset in{" "}
                      <code>.env</code>. Narratives fall back to the deterministic template.
                    </span>
                  </div>
                )}
                {tel?.source === "key_rotated" && (
                  <p style={{ color: theme.textMuted, fontSize: 12, margin: "4px 0 0" }}>
                    Key changed since the last recorded call — no telemetry for the current key yet.
                  </p>
                )}
                {tel?.source === "last_call" && tel.ok === false && c.status !== "invalid_key" && (
                  <p style={{ color: theme.textMuted, fontSize: 12, margin: "4px 0 0" }}>
                    Last call failed: {tel.error_kind}
                    {tel.checked_at ? ` · ${timeAgo(tel.checked_at)}` : ""} (not a key problem).
                  </p>
                )}
              </div>
            );
          })}
          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              marginTop: 12,
              lineHeight: 1.5,
            }}
          >
            {data.telemetry_note}
          </p>
        </div>
      )}
    </SectionCard>
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

/**
 * Entry point to the Strategy Matrix screen — a `.env`-write surface, so it
 * lives under /settings alongside every other write surface, not in top-level
 * nav. Shows a live "N modules · M disabled" summary and links to the editor.
 */
function SignalModulesLink() {
  const { data } = useApi<StrategyMatrix>(() => api.getStrategyMatrix(), []);
  const count = data?.modules.length ?? null;
  const disabledCount = data?.disabled.length ?? null;
  return (
    <Link
      to="/settings/strategy"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none", marginTop: 16 }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Signal modules</div>
          <div style={{ color: theme.textSecondary, fontSize: 13, marginTop: 2 }}>
            {count == null
              ? "Signal weights & enabled modules"
              : `${count} modules · ${disabledCount} disabled`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: 20 }}>›</span>
      </div>
    </Link>
  );
}

/**
 * Pure proxy over daemon_client.trigger_run() (see api/pilots_api.py) --
 * every branch here maps a real, documented server outcome, never a client
 * guess. `onTriggered` re-fetches /automation/status so the daemon/progress
 * rows update immediately after a successful trigger (usePoll then keeps it
 * live while the run is actually in flight).
 */
function RunNowButton({
  disabled,
  onTriggered,
}: {
  disabled: boolean;
  onTriggered: () => void;
}) {
  const { run, pending, result, error } = useMutation(() => api.triggerRun());

  const handleClick = async () => {
    await run();
    onTriggered();
  };

  return (
    <div style={{ marginTop: 12 }}>
      <Button variant="primary" block pending={pending} disabled={disabled} onClick={handleClick}>
        Run now
      </Button>
      {error && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}
      {result && !result.ok && result.error === "already_running" && (
        <div className="notice notice-info" style={{ marginTop: 10 }}>
          <span>ℹ️</span>
          <span>
            A run is already in flight
            {result.existing_run_id ? ` (${result.existing_run_id})` : ""}.
          </span>
        </div>
      )}
      {result && !result.ok && result.error === "kill_switch_active" && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>
            Kill switch active{result.kill_switch_reason ? `: ${result.kill_switch_reason}` : ""}.
          </span>
        </div>
      )}
      {result && !result.ok && result.error === "unavailable" && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>Orchestrator daemon is not reachable.</span>
        </div>
      )}
      {result?.ok && (
        <div className="notice notice-info" style={{ marginTop: 10 }}>
          <span>✅</span>
          <span>Run queued{result.run_id ? ` (${result.run_id})` : ""}.</span>
        </div>
      )}
    </div>
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

          <RunNowButton disabled={status.daemon.is_running === true} onTriggered={onRetry} />

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

/**
 * PUT /automation/schedule/interval writes ORCHESTRATOR_INTERVAL_SECONDS to
 * .env via the same allowlist-bounded writer the GUI Settings tab uses -- it
 * does NOT reach a live daemon (no runtime setter exists yet), so `onSaved`
 * only re-fetches the schedule to surface the resulting `drift` against
 * `running_value`, never claims the change is already live.
 */
function IntervalEditor({
  schedule,
  onSaved,
}: {
  schedule: AutomationSchedule;
  onSaved: () => void;
}) {
  const [value, setValue] = useState(String(schedule.interval.configured_value));
  const { run, pending, error } = useMutation((seconds: number) =>
    api.setAutomationInterval(seconds)
  );

  const parsed = Number(value);
  const invalid =
    !Number.isFinite(parsed) || parsed < 0 || parsed > 86400 || (parsed !== 0 && parsed < 60);

  const save = async () => {
    if (invalid) return;
    await run(parsed);
    onSaved();
  };

  if (!schedule.interval.writable) {
    return (
      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: 8 }}>
        {schedule.interval.note}
      </p>
    );
  }

  return (
    <div style={{ marginTop: 10 }}>
      <Input
        label="Configured interval (seconds)"
        type="number"
        inputMode="numeric"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        invalid={invalid}
        hint={invalid ? "Must be 0 or between 60 and 86400." : schedule.interval.note}
      />
      <Button
        variant="neutral"
        onClick={save}
        disabled={invalid}
        pending={pending}
        style={{ marginTop: 8 }}
      >
        Save
      </Button>
      {error && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}
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
    <SectionCard title="Schedule">
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

          <IntervalEditor schedule={schedule} onSaved={onRetry} />

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

/**
 * Pause/resume the GLOBAL kill switch (execution/kill_switch.py) -- the
 * SAME documented mechanism as docs/RUNBOOK.md §6, not a new one. Labeled
 * "Signal generation: Running/Paused", never "Schedule: on/off" -- pausing
 * does NOT stop the daemon's interval timer; cycles still run, they just
 * produce no recommendations (or submit no orders in live mode).
 *
 * Both directions require a typed reason via a confirm Modal (guards a
 * fat-fingered tap, not an attacker -- the real gates are server-side:
 * the command token, AUTOMATION_WRITES_ENABLED, and the ADVISORY_ONLY
 * check on resume). When `advisoryOnly` is false the Toggle is disabled
 * from the paused state -- resume must happen at the console while live
 * order submission is enabled.
 */
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
  const [confirmKind, setConfirmKind] = useState<"pause" | "resume" | null>(null);
  const [inputReason, setInputReason] = useState("");
  const pauseMutation = useMutation((r: string) => api.pauseAutomation(r));
  const resumeMutation = useMutation((r: string) => api.resumeAutomation(r));

  const running = !active;
  const busy = pauseMutation.pending || resumeMutation.pending;
  const resumeBlocked = !running && !advisoryOnly;

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
    <SectionCard title="Signal generation">
      <Toggle
        checked={running}
        onChange={openConfirm}
        label={running ? "Signal generation: Running" : "Signal generation: Paused"}
        disabled={resumeBlocked}
        pending={busy}
      />
      {active && reason && (
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
          ariaLabel={
            confirmKind === "pause" ? "Pause signal generation" : "Resume signal generation"
          }
          onClose={() => setConfirmKind(null)}
        >
          <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>
            {confirmKind === "pause" ? "Pause signal generation?" : "Resume signal generation?"}
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
    </SectionCard>
  );
}

/**
 * Per-pilot "Re-plan" over the EXISTING POST /pilots/{id}/follow endpoint --
 * zero new backend code. "Re-plan all" was cut from this feature: cross-
 * Pilot netting doesn't exist, so a naive loop would emit duplicate intents
 * for a symbol held by two Pilots (see the Data & Automation plan).
 */
function ActiveFollowsSection() {
  const {
    data: follows,
    loading,
    error,
    status: httpStatus,
    reload,
  } = useApi<Follow[]>(() => api.getFollows(), []);

  return (
    <SectionCard
      title="Active follows"
      sub="Re-plan recomputes and replaces output/execution_queue.json for that Pilot only."
    >
      {loading && <Loading lines={2} />}
      {!loading && error && (
        <ErrorState message={error} status={httpStatus} onRetry={reload} />
      )}
      {!loading && !error && follows && (
        follows.length === 0 ? (
          <EmptyState title="No active follows" />
        ) : (
          <div className="list">
            {follows.map((f) => (
              <FollowRow key={f.pilot_id} follow={f} />
            ))}
          </div>
        )
      )}
    </SectionCard>
  );
}

function FollowRow({ follow }: { follow: Follow }) {
  const { run, pending, result, error } = useMutation(() =>
    api.follow(follow.pilot_id, follow.amount)
  );

  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      <div className="row-main">
        <span className="row-title">{follow.pilot_id}</span>
        <span className="row-sub">{fmtUsd(follow.amount)}</span>
        {result && (
          <span
            className="row-sub"
            style={{ color: result.queue_written ? theme.growth : theme.textMuted }}
          >
            {result.queue_written
              ? `Re-planned — ${result.planned_intents.length} order(s) queued.`
              : "Preview only — execution mode is off, nothing was written."}
          </span>
        )}
        {error && (
          <span className="row-sub" style={{ color: theme.decline }}>
            {error}
          </span>
        )}
      </div>
      <Button variant="neutral" pending={pending} onClick={() => run()}>
        Re-plan
      </Button>
    </div>
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
