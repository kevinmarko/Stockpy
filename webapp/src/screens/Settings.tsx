import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type {
  AutomationSchedule,
  AutomationStatus,
  BrokerageStatus,
  Follow,
  LlmStatus,
  ProgressState,
  StrategyMatrix,
  TunablesResponse,
  PromptListResponse,
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
  Notice,
} from "../components/ui";
import { KillSwitchToggle } from "../components/KillSwitchToggle";
import { Modal } from "../components/Modal";
import { PwaStatusSection } from "../components/PwaStatusSection";
import { RobinhoodConnectForm } from "../components/RobinhoodConnectForm";
import { UniverseManager } from "../components/UniverseManager";
import { UniverseCoverage } from "../components/UniverseCoverage";
import { TabGuide } from "../components/TabGuide";
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
          fontSize: "var(--t-callout)",
          marginBottom: "var(--s-2)",
        }}
      >
        ← Back
      </button>
      <h1 className="screen-title">Data &amp; Automation</h1>
      <p className="screen-sub">
        Pipeline run status and the automated schedule, without SSHing into
        the host.
      </p>

      <TabGuide tabKey="settings" />

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

      {status && (
        <ExecutionModeSection
          advisoryOnly={status.advisory_only}
          dryRun={status.dry_run}
          alpacaPaper={status.alpaca_paper}
          onChanged={reloadStatus}
        />
      )}

      <SectionCard
        title="Tracked universe"
        sub="Add or remove any stock. Changes take effect on the next pipeline run — raw data for any symbol is explorable immediately in Data Explorer."
      >
        <UniverseManager />
        <UniverseCoverage />
      </SectionCard>

      <SignalModulesLink />

      <TunablesLink />

      <PromptRegistryLink />

      <ActiveFollowsSection />

      <BrokerageSection />

      <AiControlCenterLink />

      <div style={{ marginTop: "var(--s-4)" }}>
        <PwaStatusSection />
      </div>

      <ResetOnboardingSection />

      <p
        style={{
          color: theme.textMuted,
          fontSize: "var(--t-footnote)",
          marginTop: "var(--s-5)",
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

/**
 * Entry point to the AI Control Center screen -- a `.env`-write surface (PUT
 * /llm/setting), so it lives under /settings alongside every other write
 * surface, not in top-level nav. Shows a live "N capabilities · M ready"
 * summary plus an attention indicator, and links to the toggle/provider
 * editor + last-real-call telemetry (formerly an inline "AI providers"
 * section on this screen -- moved to its own screen once it grew a write
 * path, mirroring how Strategy Matrix already got its own /settings/strategy
 * route rather than staying inline here).
 */
function AiControlCenterLink() {
  const { data } = useApi<LlmStatus>(() => api.getLlmStatus(), []);
  const readyCount = data?.capabilities.filter((c) => c.status === "ready").length ?? null;
  const total = data?.capabilities.length ?? null;
  return (
    <Link
      to="/settings/ai"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none", marginTop: "var(--s-4)" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>
            AI providers
            {data?.attention && (
              <span aria-label="needs attention" style={{ marginLeft: 6 }}>
                ⚠️
              </span>
            )}
          </div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {total == null
              ? "LLM commentary, Gravity AI runner, Opal research"
              : `${readyCount}/${total} ready`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}

/**
 * Brokerage connection — view status and connect/disconnect Robinhood AFTER
 * onboarding, over GET /brokerage/status + POST /brokerage/{connect,disconnect}.
 * Before this, connectBrokerage was reachable only during onboarding and
 * disconnect/status had no UI at all. connect/disconnect fail closed
 * server-side when their gates aren't set (BROKERAGE_CONNECT_ENABLED +
 * FOLLOW_API_TOKEN + loopback-only -- see api/pilots_api.py); this UI renders
 * whatever the server actually returned and never echoes credentials. Reuses
 * the SAME RobinhoodConnectForm as onboarding so the intake path can't drift.
 *
 * The refreshBrokerage() button (POST /brokerage/refresh) is deliberately
 * NOT gated on `data.connected` -- unlike connect/disconnect, it never reads
 * a request body; it just calls fetch_account_snapshot(force=True)
 * server-side, which logs in with whatever RH_USERNAME/RH_PASSWORD/
 * RH_MFA_SECRET is already configured in THIS MACHINE's .env, no typed input
 * needed. That works identically regardless of what `data.connected` (a
 * client-side read of the SAME env vars, which can be stale relative to a
 * just-restarted backend, or simply not yet reflect a hand-edited .env)
 * currently reports -- so the button is offered in both branches, as a
 * faster alternative to typing credentials into RobinhoodConnectForm below.
 * An honest failure (no usable credentials configured at all) surfaces the
 * same way either way: refresh.error, never a fabricated success.
 */
function BrokerageSection() {
  const { data, loading, error, status, reload } = useApi<BrokerageStatus>(
    () => api.getBrokerageStatus(),
    []
  );
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const disconnect = useMutation(() => api.disconnectBrokerage());
  const refresh = useMutation(() => api.refreshBrokerage());

  const doDisconnect = async () => {
    await disconnect.run();
    setConfirmingDisconnect(false);
    refresh.reset(); // clear a stale refresh notice from before the disconnect
    reload();
  };

  const doRefresh = async () => {
    disconnect.reset(); // clear a stale disconnect notice from before this attempt
    await refresh.run();
    reload(); // pick up the (possibly now-populated) connected/has_account_snapshot flags
  };

  return (
    <SectionCard
      title="Brokerage"
      sub="Connect Robinhood for read-only portfolio snapshots, or disconnect to clear the stored credentials."
    >
      {loading && <Loading lines={2} />}
      {!loading && error && (
        <ErrorState message={error} status={status} onRetry={reload} />
      )}
      {!loading && !error && data && (
        <div className="list">
          {data.connected && (
            <div className="row">
              <span className="row-title">Robinhood</span>
              <MetricBadge
                label="Connected"
                value={data.has_account_snapshot ? "snapshot ready" : "no snapshot yet"}
                good={true}
              />
            </div>
          )}

          <div
            style={{
              display: "flex",
              gap: "var(--s-2-5)",
              flexWrap: "wrap",
              marginTop: data.connected ? "var(--s-3)" : 0,
            }}
          >
            <Button variant="neutral" onClick={doRefresh} pending={refresh.pending}>
              {data.connected ? "🔐 Force fresh login" : "🔐 Connect using .env credentials"}
            </Button>
            {data.connected && (
              <Button
                variant="neutral"
                onClick={() => setConfirmingDisconnect(true)}
              >
                Disconnect
              </Button>
            )}
          </div>
          <p
            style={{
              color: theme.textMuted,
              fontSize: "var(--t-caption)",
              marginTop: "var(--s-1-5)",
              marginBottom: 0,
              lineHeight: 1.4,
            }}
          >
            {data.connected ? (
              <>
                Bypasses the daily cache and re-authenticates against Robinhood
                right now — equivalent to <code>python3 main.py --refresh-account</code>.
              </>
            ) : (
              <>
                Logs in with <code>RH_USERNAME</code>/<code>RH_PASSWORD</code>
                {" "}(and <code>RH_MFA_SECRET</code>, if set) already in this
                machine's <code>.env</code> — no typing required. Haven't set
                those yet? Use the form below instead.
              </>
            )}
          </p>
          {refresh.error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              <span>⚠️</span>
              <span>{refresh.error}</span>
            </Notice>
          )}
          {refresh.result && !refresh.error && (
            <Notice variant="success" style={{ marginTop: "var(--s-2-5)" }}>
              <span>✅</span>
              <span>
                Refreshed {timeAgo(refresh.result.fetched_at)}
                {refresh.result.is_stale
                  ? " — Robinhood login failed; showing the last cached snapshot instead."
                  : ` — ${fmtUsd(refresh.result.total_equity)} total equity.`}
              </span>
            </Notice>
          )}
          {disconnect.error && (
            <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
              <span>⚠️</span>
              <span>{disconnect.error}</span>
            </Notice>
          )}

          {data.connected ? (
            <p
              style={{
                color: theme.textMuted,
                fontSize: "var(--t-caption)",
                marginTop: "var(--s-3)",
                lineHeight: 1.45,
              }}
            >
              Credentials are stored only on this local machine and are never
              shown here.
            </p>
          ) : (
            <>
              <p
                style={{
                  color: theme.textSecondary,
                  fontSize: "var(--t-body)",
                  marginTop: "var(--s-3)",
                  marginBottom: "var(--s-3)",
                }}
              >
                Not connected. Credentials go only to your local backend and are
                verified with a read-only login before anything is saved.
              </p>
              <RobinhoodConnectForm
                onConnected={() => {
                  refresh.reset(); // clear a stale refresh notice from before this connect
                  reload();
                }}
              />
            </>
          )}
        </div>
      )}

      {confirmingDisconnect && (
        <Modal
          ariaLabel="Disconnect brokerage"
          onClose={() => setConfirmingDisconnect(false)}
        >
          <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
            Disconnect Robinhood?
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0 }}>
            Clears the stored Robinhood credentials from this machine. Portfolio
            snapshots stop refreshing until you reconnect.
          </p>
          <div style={{ display: "flex", gap: "var(--s-2-5)", marginTop: "var(--s-4-5)" }}>
            <Button
              variant="neutral"
              onClick={() => setConfirmingDisconnect(false)}
              style={{ flex: 1 }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={doDisconnect}
              pending={disconnect.pending}
              style={{ flex: 2 }}
            >
              Disconnect
            </Button>
          </div>
        </Modal>
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
    <section className="card card-pad" style={{ marginTop: "var(--s-4)" }}>
      <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>{title}</h2>
      {sub && (
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-3)" }}>
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
      style={{ display: "block", textDecoration: "none", marginTop: "var(--s-4)" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Signal modules</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {count == null
              ? "Signal weights & enabled modules"
              : `${count} modules · ${disabledCount} disabled`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}

/**
 * Entry point to the Settings Manager (runtime tunables) screen — a `.env`-write
 * surface (PUT /settings/tunables), so it lives under /settings alongside every
 * other write surface, not in top-level nav. Shows a live "N tunables · M
 * groups" summary and links to the editor.
 */
function TunablesLink() {
  const { data } = useApi<TunablesResponse>(() => api.getTunables(), []);
  const groupCount = data?.groups.length ?? null;
  const fieldCount =
    data?.groups.reduce((acc, g) => acc + g.fields.length, 0) ?? null;
  return (
    <Link
      to="/settings/tunables"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none", marginTop: "var(--s-4)" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Runtime tunables</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {fieldCount == null
              ? "Sizing, forecasting & data settings"
              : `${fieldCount} tunables · ${groupCount} groups`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
      </div>
    </Link>
  );
}

/**
 * Entry point to the Prompt Registry screen — a `.env`-write-adjacent surface
 * (`PUT /prompts/pin` persists PROMPT_REGISTRY_PINS), so it lives under
 * /settings alongside every other write surface, not in top-level nav. Shows
 * a live "N prompts · M pinned" summary and links to the full table/diff/pin
 * editor. NOTE: the actual `<Route path="/settings/prompts">` is added by
 * Agent A's integration pass (see App.tsx's sole-editor convention) — this
 * card is wired ahead of that route landing, matching the pattern every other
 * *Link component here already uses.
 */
function PromptRegistryLink() {
  const { data } = useApi<PromptListResponse>(() => api.getPrompts(), []);
  const count = data?.prompts.length ?? null;
  const pinnedCount = data?.prompts.filter((p) => p.pinned_version != null).length ?? null;
  return (
    <Link
      to="/settings/prompts"
      className="card card-pad"
      style={{ display: "block", textDecoration: "none", marginTop: "var(--s-4)" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Prompt Registry</div>
          <div style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: "var(--s-0-5)" }}>
            {count == null
              ? "Version control for AI-facing instructions"
              : `${count} prompts · ${pinnedCount} pinned`}
          </div>
        </div>
        <span style={{ color: theme.textMuted, fontSize: "var(--t-title)" }}>›</span>
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
  // The pause/resume UI is the shared KillSwitchToggle — the SAME control the
  // Agentic Trading tab's Controls section renders (UX backlog finding #6:
  // one kill switch, one component, no drift). `showReason` renders the inline
  // "Reason: ..." line here, which this screen has always shown.
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
