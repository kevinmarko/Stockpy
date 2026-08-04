import { useEffect, useState } from "react";
import { useDebounce } from "../hooks/useDebounce";
import { api } from "../api/client";
import type {
  AutomationSchedule,
  AutomationStatus,
  BrokerageStatus,
  ProgressState,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { useMutation } from "../hooks/useMutation";
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  Loading,
  MetricBadge,
  Notice,
} from "../components/ui";
import { Modal } from "../components/Modal";
import { Toggle } from "../components/Toggle";
import {
  useAutoRefresh,
  CATEGORY_INTERVAL_RULES,
  type AutoRefreshCategory,
} from "../components/AutoRefreshContext";
import { fmtAge, fmtDate, timeAgo } from "../format";
import { theme } from "../theme";
import { SectionCard } from "../components/SectionCard";
import { TabGuide } from "../components/TabGuide";

export function SettingsData() {
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

  const {
    data: brokerageData,
  } = useApi<BrokerageStatus>(() => api.getBrokerageStatus(), []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
      <div>
        <h2 style={{ margin: "0 0 var(--s-1)", fontSize: "var(--t-title)" }}>Data &amp; Automation</h2>
        <p style={{ color: theme.textSecondary, margin: 0, fontSize: "var(--t-body)" }}>
          Pipeline run status and the automated schedule, without SSHing into the host.
        </p>
      </div>

      <TabGuide tabKey="settings-data" />

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

      <AutoRefreshSection brokerageStatus={brokerageData} />
    </div>
  );
}

const ROBINHOOD_PRESETS_MIN: { label: string; min: number }[] = [
  { label: "15m", min: 15 },
  { label: "30m", min: 30 },
  { label: "1h", min: 60 },
  { label: "4h", min: 240 },
  { label: "12h", min: 720 },
];


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
  // Gated on `!status` (no data yet), not `loading`/`error` alone -- a
  // background reload (e.g. RunNowButton's onTriggered() re-fetching status
  // right after a trigger) sets `loading` true again for an instant. Hiding
  // the whole block on every `loading` flip would unmount RunNowButton mid
  // reload and discard the "Run queued" confirmation it had just rendered
  // (stale-while-revalidate keeps showing the last-known `status` instead).
  return (
    <SectionCard title="Pipeline status">
      {loading && !status && <Loading lines={3} />}
      {!loading && error && !status && (
        <ErrorState message={error} status={httpStatus} onRetry={onRetry} />
      )}
      {status && (
        <div className="list">
          <div className="row">
            <span className="row-title">Daemon</span>
            <MetricBadge
              label={status.daemon.alive ? "Alive" : "Not reachable"}
              value={
                status.daemon.source === "none"
                  ? "no signal"
                  : status.daemon.source === "daemon_json"
                    ? status.daemon.pid_alive === false
                      ? "stopped — process not running"
                      : status.daemon.pid_alive === true
                        ? "process alive, API not responding"
                        : "last known state"
                    : "live"
              }
              good={status.daemon.alive}
            />
          </div>

          <RestartDaemonControl
            runInFlight={status.daemon.is_running === true}
            onRestarted={onRetry}
          />

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
              <span style={{ color: theme.textMuted, fontSize: "var(--t-body)" }}>—</span>
            )}
          </div>

          {status.last_run_source === "state_snapshot" && (
            <Notice variant="info" style={{ marginTop: "var(--s-2-5)" }}>
              <span>ℹ️</span>
              <span>
                No run record — the daemon has never triggered a run this
                process lifetime (or restarted since). Last pipeline output:{" "}
                {fmtAge(status.pipeline.snapshot_age_seconds)}.
              </span>
            </Notice>
          )}

          <div className="row">
            <span className="row-title">Last pipeline output</span>
            <span style={{ color: theme.textSecondary, fontSize: "var(--t-body)" }}>
              {fmtAge(status.pipeline.snapshot_age_seconds)}
              {status.pipeline.snapshot_age_source === "mtime" && " (file time)"}
            </span>
          </div>

          {status.progress && !status.progress.is_terminal && !status.progress.stale && (
            <ProgressDetail progress={status.progress} />
          )}

          {status.kill_switch.active && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              <span>⚠️</span>
              <span>
                Kill switch active{status.kill_switch.reason ? `: ${status.kill_switch.reason}` : ""}.
              </span>
            </Notice>
          )}

          <RunNowButton disabled={status.daemon.is_running === true} onTriggered={onRetry} />

          <ErrorsSubsection errors={status.errors} />

          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              marginTop: "var(--s-3)",
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


/**
 * RestartDaemonControl — surfaces the already-built, already-wired
 * `POST /daemon/restart` (`api.restartDaemon()`) here, next to the "Daemon"
 * status row. Previously this exact action was reachable ONLY from the
 * Runtime Tunables screen's `env_drift.detected` notice
 * (`SettingsManager.tsx`), guarded by nothing more than a bare click — no
 * confirmation step at all, just `onClick={async () => { await
 * api.restartDaemon(); alert(res.message); }}`. That was tolerable there
 * because it only appears after the operator has just changed a setting and
 * is already deep in an edit flow. This is a more prominent, always-visible
 * placement of the SAME action, so it gets a stronger guard: a
 * typed-confirmation Modal, reusing the shared `Modal` scaffold (already
 * generic across 5+ call sites in this file) rather than `KillSwitchToggle`
 * itself, which is a self-contained pause/resume switch hardwired to a
 * `reason` string that gets persisted server-side as an audit trail --
 * `restartDaemon()` takes no parameters at all, so a "reason" input here
 * would be theater (typed, then discarded). Instead the gate is "type
 * RESTART to confirm", a stronger fat-finger guard than a free-text reason
 * (nothing typed in accidentally satisfies it) and an honest one (nothing is
 * silently sent anywhere).
 *
 * The confirmation copy carries forward `/daemon/restart`'s own documented
 * caveat verbatim (see `api/control_api.py`'s `restart_daemon` docstring):
 * whether the process actually comes back depends entirely on the process
 * supervisor it's running under. This is NOT a "stop and don't restart"
 * control -- it always requests a restart; it just can't promise the OS
 * will honor it, so the copy says so plainly rather than either hiding the
 * risk or overstating it.
 *
 * `runInFlight` disables the button while `daemon.is_running` -- mirroring
 * `control_api.py`'s own 409 guard (`restart_daemon` rejects while a run is
 * active) -- so the UI prevents the doomed request instead of surfacing a
 * raw 409; the server-side guard remains the authority either way.
 *
 * No client-side capability/auth pre-check (e.g. "is `ORCHESTRATOR_DAEMON_
 * TOKEN` configured") is added here, matching how `SettingsManager.tsx`'s
 * existing usage handles it: the control always renders, and an auth/gating
 * failure (missing command token, `Daemon not available`, etc.) surfaces
 * through the same `mutation.error` path as any other failure -- there is no
 * status field this screen could read to predict that failure in advance.
 *
 * Mounted as a sibling inside `PipelineStatusSection`'s `{status && (...)}`
 * branch (not gated on `loading`), so it inherits the stale-while-revalidate
 * fix (see that function's own comment) for free: a background status
 * reload after a restart attempt never unmounts this component or discards
 * its confirmation/result state, the same failure mode `RunNowButton` had
 * before that fix landed.
 */
function RestartDaemonControl({
  runInFlight,
  onRestarted,
}: {
  runInFlight: boolean;
  onRestarted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const mutation = useMutation(() => api.restartDaemon());
  const confirmed = typed.trim().toUpperCase() === "RESTART";

  const openConfirm = () => {
    setTyped("");
    mutation.reset();
    setConfirming(true);
  };

  const confirmRestart = async () => {
    await mutation.run();
    setConfirming(false);
    onRestarted();
  };

  return (
    <div style={{ marginTop: "var(--s-2-5)" }}>
      <Button
        variant="neutral"
        onClick={openConfirm}
        disabled={runInFlight}
        data-testid="restart-daemon-button"
      >
        Restart daemon
      </Button>
      {runInFlight && (
        <p
          style={{
            color: theme.textMuted,
            fontSize: "var(--t-caption)",
            marginTop: "var(--s-1-5)",
          }}
        >
          Disabled while a pipeline run is active.
        </p>
      )}

      {mutation.error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }} data-testid="restart-daemon-error">
          <span>⚠️</span>
          <span>{mutation.error}</span>
        </Notice>
      )}
      {mutation.result && (
        <Notice variant="success" style={{ marginTop: "var(--s-2-5)" }} data-testid="restart-daemon-success">
          <span>✅</span>
          <span>{mutation.result.message}</span>
        </Notice>
      )}

      {confirming && (
        <Modal ariaLabel="Restart daemon" onClose={() => setConfirming(false)}>
          <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
            Restart the daemon process?
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
            Exits the running orchestrator process so it can come back up
            with any freshly-written settings applied. Whether it actually
            comes back depends on how it's being run: a process supervisor
            with auto-restart (systemd <code>Restart=always</code>, launchd{" "}
            <code>KeepAlive</code>) relaunches it automatically. If your
            deployment doesn't auto-restart the daemon process, you may need
            to relaunch it manually at the host machine.
          </p>
          <Input
            label='Type "RESTART" to confirm'
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            hint="Required."
          />
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button variant="neutral" onClick={() => setConfirming(false)} style={{ flex: 1 }}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={confirmRestart}
              disabled={!confirmed}
              pending={mutation.pending}
              style={{ flex: 2 }}
              data-testid="restart-daemon-confirm"
            >
              Restart
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}


/**
 * Per-stage breakdown of an in-flight run. `stage_total` is a count, not a
 * named list (the daemon never serializes the other stage names), so the
 * dots are rendered generically -- done/current/pending -- rather than
 * labeled, to avoid guessing at stage names the API never sent (CONSTRAINT #4).
 */
function ProgressDetail({ progress }: { progress: ProgressState }) {
  return (
    <div className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="row-title">In progress</span>
        <span style={{ color: theme.accent, fontSize: "var(--t-body)" }}>
          {progress.stage} ({progress.stage_index + 1}/
          {progress.stage_total}) · {progress.percent.toFixed(0)}%
        </span>
      </div>

      {progress.stage_total > 0 && (
        <div
          role="img"
          aria-label={`Stage ${progress.stage_index + 1} of ${progress.stage_total}`}
          style={{ display: "flex", gap: "var(--s-1)", marginTop: "var(--s-2)" }}
        >
          {Array.from({ length: progress.stage_total }, (_, i) => (
            <span
              key={i}
              data-testid="progress-stage-dot"
              data-state={
                i < progress.stage_index
                  ? "done"
                  : i === progress.stage_index
                    ? "current"
                    : "pending"
              }
              style={{
                flex: 1,
                height: 4,
                borderRadius: 2,
                background:
                  i < progress.stage_index
                    ? theme.growth
                    : i === progress.stage_index
                      ? theme.accent
                      : theme.surface3,
              }}
            />
          ))}
        </div>
      )}

      {progress.symbols_total > 0 && (
        <div className="row-sub" style={{ marginTop: "var(--s-1-5)" }}>
          {progress.symbols_done}/{progress.symbols_total} symbols in this stage
        </div>
      )}

      {progress.message && (
        <div className="row-sub" style={{ marginTop: "var(--s-0-5)", color: theme.textMuted }}>
          {progress.message}
        </div>
      )}
    </div>
  );
}


function ErrorsSubsection({ errors }: { errors: AutomationStatus["errors"] }) {
  if (errors.entry_count === 0) {
    return (
      <div style={{ marginTop: "var(--s-3)" }}>
        <div className="row-sub" style={{ marginBottom: "var(--s-1)" }}>
          Errors · as of {timeAgo(errors.generated_at)}
        </div>
        <EmptyState title="No errors" hint="The last run completed cleanly." />
      </div>
    );
  }
  return (
    <div style={{ marginTop: "var(--s-3)" }}>
      <div className="row-sub" style={{ marginBottom: "var(--s-1)" }}>
        Errors ({errors.entry_count}) · as of {timeAgo(errors.generated_at)}
      </div>
      <Notice variant="warn">
        <span>⚠️</span>
        <span>
          {errors.entry_count} symbol{errors.entry_count === 1 ? "" : "s"} failed on
          the last run{errors.entries.length < errors.entry_count
            ? ` (showing ${errors.entries.length})`
            : ""}
          .
        </span>
      </Notice>
      <div className="list" style={{ marginTop: "var(--s-1)" }}>
        {errors.entries.map((entry, i) => (
          <div className="row" key={i} style={{ padding: "var(--s-1-5) 0" }}>
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
      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-2)" }}>
        {schedule.interval.note}
      </p>
    );
  }

  return (
    <div style={{ marginTop: "var(--s-2-5)" }}>
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
        style={{ marginTop: "var(--s-2)" }}
      >
        Save
      </Button>
      {error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span>⚠️</span>
          <span>{error}</span>
        </Notice>
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
              <span style={{ color: theme.textSecondary, fontSize: "var(--t-body)" }}>
                {schedule.interval.running_value == null
                  ? "unknown"
                  : `${schedule.interval.running_value}s`}
              </span>
            </div>
          </div>
          {schedule.interval.drift && (
            <Notice variant="info" style={{ marginTop: "var(--s-2-5)" }}>
              <span>ℹ️</span>
              <span>
                Running: {schedule.interval.running_value}s · Configured:{" "}
                {schedule.interval.configured_value}s. Restart the daemon to
                apply the configured value.
              </span>
            </Notice>
          )}

          <IntervalEditor schedule={schedule} onSaved={onRetry} />

          <div style={{ marginTop: "var(--s-3-5)" }}>
            <div className="row-sub" style={{ marginBottom: "var(--s-1-5)" }}>
              Cron ({schedule.cron.source})
            </div>
            <div className="list">
              {schedule.cron.entries.map((entry, i) => (
                <div className="row" key={i} style={{ alignItems: "flex-start" }}>
                  <div className="row-main">
                    <span className="row-title" style={{ fontFamily: "monospace", fontSize: "var(--t-body)" }}>
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
                marginTop: "var(--s-2)",
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


function AutoRefreshSection({
  brokerageStatus,
}: {
  /** Fetched once at the Settings() level and passed down -- see that
   * component's comment for why this can't be a second independent
   * useApi(getBrokerageStatus) call site. */
  brokerageStatus: BrokerageStatus | null;
}) {
  const {
    autoRefreshEnabled,
    pauseWhenMarketClosed,
    autoRefreshIntervalMs,
    portfolioRefreshEnabled,
    dashboardRefreshEnabled,
    signalsRefreshEnabled,
    observabilityRefreshEnabled,
    optionsRefreshEnabled,
    robinhoodRefreshEnabled,
    safetyTelemetryEnabled,
    isTabVisible,
    isMarketOpen,
    categoryIntervalMs,
    setAutoRefreshEnabled,
    setPauseWhenMarketClosed,
    setAutoRefreshIntervalMs,
    setCategoryRefreshEnabled,
    setCategoryIntervalMs,
    setSafetyTelemetryEnabled,
  } = useAutoRefresh();

  const [customInputSec, setCustomInputSec] = useState<string>(
    String(Math.round(autoRefreshIntervalMs / 1000))
  );

  // Sync internal input state when external autoRefreshIntervalMs changes (e.g. preset clicked)
  useEffect(() => {
    setCustomInputSec(String(Math.round(autoRefreshIntervalMs / 1000)));
  }, [autoRefreshIntervalMs]);

  const parsedCustomSec = parseInt(customInputSec, 10);
  const customInvalid =
    isNaN(parsedCustomSec) || parsedCustomSec < 5 || parsedCustomSec > 86400;

  // Debounce custom interval input changes (500ms).
  const debouncedCustomSec = useDebounce(parsedCustomSec, 500);

  useEffect(() => {
    if (customInvalid) return;
    if (debouncedCustomSec * 1000 !== autoRefreshIntervalMs) {
      setAutoRefreshIntervalMs(debouncedCustomSec * 1000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedCustomSec, customInvalid, setAutoRefreshIntervalMs]);

  const presets = [
    { label: "15s", ms: 15_000 },
    { label: "30s", ms: 30_000 },
    { label: "60s", ms: 60_000 },
    { label: "2m", ms: 120_000 },
    { label: "5m", ms: 300_000 },
  ];

  const currentSec = Math.round(autoRefreshIntervalMs / 1000);

  // Only the 5 ordinary categories -- Robinhood is a structurally different
  // concern (a real broker login, not a local DB read) with its own section
  // below, and deliberately excluded from this count.
  const enabledCategoryCount = [
    portfolioRefreshEnabled,
    dashboardRefreshEnabled,
    signalsRefreshEnabled,
    observabilityRefreshEnabled,
    optionsRefreshEnabled,
  ].filter(Boolean).length;

  // Three real states with real precedence (market-closed outlasts a tab
  // hidden for seconds, so it's checked first), plus the "on but nothing
  // selected" case -- the same class of lie ("Active" while doing nothing)
  // this whole card exists to avoid.
  let statusValue = "Disabled";
  let statusGood: boolean | null = null;
  if (autoRefreshEnabled) {
    if (pauseWhenMarketClosed && !isMarketOpen) {
      statusValue = "Paused — market closed";
      statusGood = false;
    } else if (!isTabVisible) {
      // Correct but nearly unobservable in practice -- nobody reads this
      // badge on a hidden tab. Paired with static prose below ("Polling
      // also pauses while this tab is in the background").
      statusValue = "Paused — tab hidden";
      statusGood = false;
    } else if (enabledCategoryCount === 0) {
      statusValue = "On, but no categories selected";
      statusGood = false;
    } else {
      statusValue = `Active — ${enabledCategoryCount} of 5 categories, ${currentSec}s`;
      statusGood = true;
    }
  }

  return (
    <section className="card card-pad" data-testid="auto-refresh-section">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-2)" }}>
        <h2 style={{ fontSize: "var(--t-input)", margin: 0 }}>Data Auto-Refresh</h2>
        <MetricBadge label="Auto-refresh" value={statusValue} good={statusGood} />
      </div>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", margin: "0 0 var(--s-2)", lineHeight: 1.5 }}>
        Automatically reload screen data at a configured interval. Polling
        also pauses while this tab is in the background, and (if enabled
        below) while the market is closed. Two heavy reads keep their own
        slower floor no matter how short an interval you pick: the top bar's
        macro regime read and the Console's host telemetry.
      </p>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-footnote)", margin: "0 0 var(--s-3)", lineHeight: 1.5 }}>
        Note: Console, Agentic status, and dashboard alerts no longer
        auto-refresh out of the box under this master switch — turn it on
        here to restore that.
      </p>

      {/* Master Toggle */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)", padding: "var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-sm)" }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: "var(--t-label)" }}>Enable Auto-Refresh</div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>
            Master switch for screen data auto-polling. Safety telemetry
            below is separate.
          </div>
        </div>
        <Toggle
          label="Enable Auto-Refresh"
          checked={autoRefreshEnabled}
          onChange={(val) => setAutoRefreshEnabled(val)}
          dataTestId="auto-refresh-master-toggle"
        />
      </div>

      {/* Pause When Market Closed Toggle */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-3)", padding: "var(--s-2)", background: "var(--surface-2)", borderRadius: "var(--r-sm)" }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: "var(--t-label)" }}>Pause When Market Closed</div>
          <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>Pause auto-polling on weekends &amp; after-hours</div>
        </div>
        <Toggle
          label="Pause When Market Closed"
          checked={pauseWhenMarketClosed}
          onChange={(val) => setPauseWhenMarketClosed(val)}
          dataTestId="auto-refresh-pause-closed-toggle"
        />
      </div>

      {/* Interval Selector */}
      <div style={{ marginBottom: "var(--s-4)" }}>
        <div style={{ fontWeight: 600, fontSize: "var(--t-label)", marginBottom: "var(--s-1)" }}>
          Refresh Interval
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", alignItems: "center", marginBottom: "var(--s-2)" }}>
          {presets.map((p) => (
            <button
              key={p.ms}
              className={`btn btn-sm ${autoRefreshIntervalMs === p.ms ? "btn-primary" : "btn-subtle"}`}
              onClick={() => {
                setAutoRefreshIntervalMs(p.ms);
                setCustomInputSec(String(p.ms / 1000));
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
        <Input
          label="Custom duration (sec)"
          type="number"
          inputMode="numeric"
          min={5}
          max={86400}
          value={customInputSec}
          onChange={(e) => setCustomInputSec(e.target.value)}
          invalid={customInvalid}
          hint={customInvalid ? "Must be between 5 and 86400 seconds." : undefined}
        />
      </div>

      {/* Screen Categories */}
      <div style={{ borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-3)" }}>
        <div style={{ fontWeight: 600, fontSize: "var(--t-label)", marginBottom: "var(--s-2)" }}>
          Active Categories
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--s-2)" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={portfolioRefreshEnabled}
              onChange={(e) => setCategoryRefreshEnabled("portfolio", e.target.checked)}
            />
            Portfolio &amp; Pilots
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={dashboardRefreshEnabled}
              onChange={(e) => setCategoryRefreshEnabled("dashboard", e.target.checked)}
            />
            Main Dashboard
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={signalsRefreshEnabled}
              onChange={(e) => setCategoryRefreshEnabled("signals", e.target.checked)}
            />
            Signals &amp; Strategy Matrix
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={observabilityRefreshEnabled}
              onChange={(e) => setCategoryRefreshEnabled("observability", e.target.checked)}
            />
            Observability &amp; Telemetry
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-2)", fontSize: "var(--t-caption)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={optionsRefreshEnabled}
              onChange={(e) => setCategoryRefreshEnabled("options", e.target.checked)}
            />
            Options &amp; Market Analytics
          </label>
        </div>
      </div>

      {/* Safety telemetry -- a SEPARATE switch from the master above. Its own
          bordered section, not folded into "Active Categories": governing
          only the kill-switch/heartbeat poll, on by default, and deliberately
          NOT gated by market session or tab visibility (see TopStatusBar). */}
      <div style={{ borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-3)", marginTop: "var(--s-3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ paddingRight: "var(--s-3)" }}>
            <div style={{ fontWeight: 600, fontSize: "var(--t-label)" }}>Safety telemetry</div>
            <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginTop: "var(--s-0-5)", lineHeight: 1.4 }}>
              Independent of the master switch above — this governs only the
              kill-switch/heartbeat poll in the top bar. It keeps running
              even when the market is closed or auto-refresh is off: a stale
              kill-switch reading is a safety risk, not a battery
              optimization, so it isn't gated the same way.
            </div>
          </div>
          <Toggle
            label="Safety telemetry"
            checked={safetyTelemetryEnabled}
            onChange={(val) => setSafetyTelemetryEnabled(val)}
            dataTestId="auto-refresh-safety-telemetry-toggle"
          />
        </div>
      </div>

      <RobinhoodRefreshSection
        robinhoodRefreshEnabled={robinhoodRefreshEnabled}
        setCategoryRefreshEnabled={setCategoryRefreshEnabled}
        categoryIntervalMs={categoryIntervalMs}
        setCategoryIntervalMs={setCategoryIntervalMs}
        brokerageStatus={brokerageStatus}
      />
    </section>
  );
}


/**
 * Robinhood auto-refresh -- its own bordered block, NOT a peer inside the
 * "Active Categories" grid: this category alone triggers a real broker
 * login (see ROBINHOOD_AUTO_REFRESH_ENABLED in CLAUDE.md), a structurally
 * different cost from every other category's local DB read, so it defaults
 * OFF, uses minute-granularity presets with a 15-minute floor, and surfaces
 * (read-only) whether the server is even willing to log in on its own.
 *
 * Does NOT make the server-side ROBINHOOD_AUTO_REFRESH_ENABLED flag writable
 * from here -- it's already writable via the Runtime Tunables editor, which
 * correctly shows the ".env-only, applies on next daemon restart" contract;
 * duplicating a toggle here would look instant when it isn't.
 */
function RobinhoodRefreshSection({
  robinhoodRefreshEnabled,
  setCategoryRefreshEnabled,
  categoryIntervalMs,
  setCategoryIntervalMs,
  brokerageStatus,
}: {
  robinhoodRefreshEnabled: boolean;
  setCategoryRefreshEnabled: (category: AutoRefreshCategory, enabled: boolean) => void;
  categoryIntervalMs: Partial<Record<AutoRefreshCategory, number>>;
  setCategoryIntervalMs: (category: AutoRefreshCategory, ms: number) => void;
  /** Passed down from Settings() -- a second independent
   * useApi(getBrokerageStatus) call site here would double the real network
   * calls (and desync any caller counting them) alongside BrokerageSection's
   * own fetch of the exact same endpoint. */
  brokerageStatus: BrokerageStatus | null;
}) {
  const rule = CATEGORY_INTERVAL_RULES.robinhood;
  const robinhoodDefaultMs = rule?.default ?? 3_600_000;
  const robinhoodFloorMs = rule?.min ?? 900_000;
  const floorMin = Math.round(robinhoodFloorMs / 60_000);

  const robinhoodIntervalMs = categoryIntervalMs.robinhood ?? robinhoodDefaultMs;
  const robinhoodIntervalMin = Math.round(robinhoodIntervalMs / 60_000);

  const [customMin, setCustomMin] = useState<string>(String(robinhoodIntervalMin));

  useEffect(() => {
    setCustomMin(String(Math.round(robinhoodIntervalMs / 60_000)));
  }, [robinhoodIntervalMs]);

  const parsedCustomMin = parseInt(customMin, 10);
  const customInvalid =
    isNaN(parsedCustomMin) || parsedCustomMin < floorMin || parsedCustomMin > 1440;

  useEffect(() => {
    if (customInvalid) return;
    const timer = setTimeout(() => {
      setCategoryIntervalMs("robinhood", parsedCustomMin * 60_000);
    }, 500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customMin, customInvalid, setCategoryIntervalMs]);

  const serverGateEnabled = brokerageStatus?.auto_refresh_enabled;

  return (
    <div style={{ borderTop: `1px solid ${theme.border}`, paddingTop: "var(--s-3)", marginTop: "var(--s-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-1-5)" }}>
        <div style={{ fontWeight: 600, fontSize: "var(--t-label)" }}>Robinhood</div>
        <Toggle
          label="Robinhood auto-refresh"
          checked={robinhoodRefreshEnabled}
          onChange={(val) => setCategoryRefreshEnabled("robinhood", val)}
          dataTestId="auto-refresh-robinhood-toggle"
        />
      </div>
      <p style={{ color: theme.textMuted, fontSize: "var(--t-caption)", margin: "0 0 var(--s-2)", lineHeight: 1.5 }}>
        Every refresh performs a real Robinhood login. Robinhood's own
        account snapshot only refreshes about every 20 hours, so anything
        under an hour is wasted work — and repeated automated logins are
        what can trigger security challenges. Minimum 15 minutes.
      </p>

      {brokerageStatus && (
        <div style={{ marginBottom: "var(--s-2)" }}>
          <MetricBadge
            label="Backend login gate"
            value={serverGateEnabled ? "on" : "off — cached data only"}
            good={serverGateEnabled ?? null}
          />
        </div>
      )}
      {brokerageStatus && serverGateEnabled === false && (
        <Notice variant="warn" style={{ marginBottom: "var(--s-2)" }}>
          <span>⚠️</span>
          <span>
            The backend is configured not to log in to Robinhood on its own,
            so this toggle will refresh cached data only — it cannot produce
            a fresh snapshot. Change <code>ROBINHOOD_AUTO_REFRESH_ENABLED</code>{" "}
            in Settings Manager (applies on the next daemon restart).
          </span>
        </Notice>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)", alignItems: "center", marginBottom: "var(--s-2)" }}>
        {ROBINHOOD_PRESETS_MIN.map((p) => (
          <button
            key={p.min}
            className={`btn btn-sm ${robinhoodIntervalMin === p.min ? "btn-primary" : "btn-subtle"}`}
            onClick={() => setCategoryIntervalMs("robinhood", p.min * 60_000)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <Input
        label="Custom duration (min)"
        type="number"
        inputMode="numeric"
        min={floorMin}
        max={1440}
        value={customMin}
        onChange={(e) => setCustomMin(e.target.value)}
        invalid={customInvalid}
        hint={customInvalid ? `Must be at least ${floorMin} minutes.` : undefined}
      />
    </div>
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
    <div style={{ marginTop: "var(--s-3)" }}>
      <Button variant="primary" block pending={pending} disabled={disabled} onClick={handleClick}>
        Run now
      </Button>
      {error && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span>⚠️</span>
          <span>{error}</span>
        </Notice>
      )}
      {result && !result.ok && result.error === "already_running" && (
        <Notice variant="info" style={{ marginTop: "var(--s-2-5)" }}>
          <span>ℹ️</span>
          <span>
            A run is already in flight
            {result.existing_run_id ? ` (${result.existing_run_id})` : ""}.
          </span>
        </Notice>
      )}
      {result && !result.ok && result.error === "kill_switch_active" && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span>⚠️</span>
          <span>
            Kill switch active{result.kill_switch_reason ? `: ${result.kill_switch_reason}` : ""}.
          </span>
        </Notice>
      )}
      {result && !result.ok && result.error === "unavailable" && (
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span>⚠️</span>
          <span>Orchestrator daemon is not reachable.</span>
        </Notice>
      )}
      {result?.ok && (
        <Notice variant="success" style={{ marginTop: "var(--s-2-5)" }}>
          <span>✅</span>
          <span>Run queued{result.run_id ? ` (${result.run_id})` : ""}.</span>
        </Notice>
      )}
    </div>
  );
}
