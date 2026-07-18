/**
 * PipelineDashboard.tsx — the orchestrator daemon's live status + stage-scoped
 * run triggers (api/control_api.py, GET /status + POST /run|/pipeline/data|
 * /pipeline/metrics). Distinct from Settings' "Data & Automation" view (which
 * reads the composed pilots_api /automation/status): this is the raw daemon
 * the trigger buttons act directly against.
 *
 * Honesty (CONSTRAINT #4): a run with no recorded `mode`, no `finished_at`, or
 * no `duration_seconds` renders "—", never a fabricated "FULL"/"0.0s". A failed
 * run's real `error` is shown, never softened. Polling engages ONLY while a run
 * is actually in flight (battery), mirroring the Settings screen.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { ControlStatus, RunRecord } from "../api/types";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { useMutation } from "../hooks/useMutation";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  StaleDataNotice,
} from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { timeAgo } from "../format";
import { theme } from "../theme";

type TriggerKind = "full" | "data" | "metrics";

/** Maps the daemon's documented non-2xx trigger responses to plain text. */
async function triggerControl<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 409) throw new Error("A run is already in flight.");
      if (e.status === 423)
        throw new Error("Kill switch is active — the pipeline is paused.");
      if (e.status === 401 || e.status === 403)
        throw new Error(
          "Not authorized to trigger runs (the daemon's command token is not configured)."
        );
    }
    throw e;
  }
}

function StateBadge({ state }: { state: RunRecord["state"] }) {
  const cls =
    state === "succeeded"
      ? "badge badge-good"
      : state === "failed"
        ? "badge badge-bad"
        : "badge badge-warn"; // running | queued — amber (pending)
  return <span className={cls}>{state}</span>;
}

function StatusBanner({ status }: { status: ControlStatus }) {
  const running = status.is_running;
  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span
          aria-hidden
          style={{
            width: 12,
            height: 12,
            borderRadius: "50%",
            flex: "0 0 auto",
            background: running
              ? theme.caution
              : status.daemon_alive
                ? theme.growth
                : theme.textMuted,
          }}
        />
        <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>
          {running ? "Running" : status.daemon_alive ? "Idle" : "Daemon offline"}
        </div>
      </div>

      {running && status.current_run_id && (
        <p style={{ color: theme.textSecondary, fontSize: 13, margin: "8px 0 0" }}>
          Current run:{" "}
          <span className="num" style={{ fontFamily: "monospace" }}>
            {status.current_run_id}
          </span>
        </p>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        <span className={status.engines_warm ? "badge badge-good" : "badge badge-neutral"}>
          Engines {status.engines_warm ? "warm" : "cold"}
        </span>
        <span className="badge badge-neutral">
          Interval {status.interval_seconds == null ? "—" : `${status.interval_seconds}s`}
        </span>
        {status.advisory_only && <span className="badge badge-neutral">Advisory only</span>}
        {status.dry_run && <span className="badge badge-warn">Dry run</span>}
      </div>

      {status.kill_switch_active && (
        <div className="notice notice-warn" style={{ marginTop: 12 }}>
          <span aria-hidden>⚠️</span>
          <span>
            Kill switch active
            {status.kill_switch_reason ? `: ${status.kill_switch_reason}` : ""}. New
            runs are paused.
          </span>
        </div>
      )}
    </section>
  );
}

function Controls({
  disabled,
  onTriggered,
}: {
  disabled: boolean;
  onTriggered: () => void;
}) {
  const [pendingKind, setPendingKind] = useState<TriggerKind | null>(null);
  const trigger = useMutation((kind: TriggerKind) => {
    if (kind === "data") return triggerControl(() => api.postControlPipelineData());
    if (kind === "metrics") return triggerControl(() => api.postControlPipelineMetrics());
    return triggerControl(() => api.postControlRun());
  });

  const handle = async (kind: TriggerKind) => {
    setPendingKind(kind);
    await trigger.run(kind);
    setPendingKind(null);
    onTriggered();
  };

  const busy = disabled || trigger.pending;

  return (
    <section className="card card-pad" style={{ marginTop: 16 }}>
      <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>Trigger a run</h2>
      <p style={{ color: theme.textSecondary, fontSize: 13, marginTop: 0, marginBottom: 12 }}>
        Runs are handled by the daemon; this page reflects whatever the daemon
        actually accepted.
      </p>

      <Button
        variant="primary"
        block
        pending={pendingKind === "full" && trigger.pending}
        disabled={busy}
        onClick={() => handle("full")}
        data-testid="trigger-full"
      >
        Run full advisory pipeline
      </Button>

      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <div style={{ flex: 1 }}>
          <Button
            block
            pending={pendingKind === "data" && trigger.pending}
            disabled={busy}
            onClick={() => handle("data")}
            data-testid="trigger-data"
          >
            Data only
          </Button>
        </div>
        <div style={{ flex: 1 }}>
          <Button
            block
            pending={pendingKind === "metrics" && trigger.pending}
            disabled={busy}
            onClick={() => handle("metrics")}
            data-testid="trigger-metrics"
          >
            Metrics only
          </Button>
        </div>
      </div>

      {trigger.error && (
        <div className="notice notice-warn" style={{ marginTop: 10 }}>
          <span aria-hidden>⚠️</span>
          <span>{trigger.error}</span>
        </div>
      )}
      {trigger.result && (
        <div className="notice notice-info" style={{ marginTop: 10 }}>
          <span aria-hidden>✅</span>
          <span>
            {trigger.result.state ?? "queued"}
            {trigger.result.run_id ? ` — ${trigger.result.run_id}` : ""}
            {"mode" in trigger.result && trigger.result.mode
              ? ` (${trigger.result.mode})`
              : ""}
            .
          </span>
        </div>
      )}
    </section>
  );
}

function RunsTable({ runs }: { runs: RunRecord[] }) {
  return (
    <table
      style={{
        width: "100%",
        borderCollapse: "collapse",
        textAlign: "left",
        fontSize: 13,
      }}
    >
      <thead>
        <tr style={{ borderBottom: `1px solid ${theme.borderStrong}` }}>
          <th style={{ padding: 8 }}>Run</th>
          <th style={{ padding: 8 }}>Mode</th>
          <th style={{ padding: 8 }}>State</th>
          <th style={{ padding: 8 }}>Started</th>
          <th style={{ padding: 8 }}>Duration</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.run_id} style={{ borderBottom: `1px solid ${theme.surface3}` }}>
            <td style={{ padding: 8, fontFamily: "monospace", fontSize: 12 }}>
              {r.run_id}
            </td>
            <td style={{ padding: 8 }}>
              {r.mode ? (
                <span className="chip">{r.mode.toUpperCase()}</span>
              ) : (
                <span style={{ color: theme.textMuted }}>—</span>
              )}
            </td>
            <td style={{ padding: 8 }}>
              <StateBadge state={r.state} />
              {r.error && (
                <div
                  style={{ color: theme.textMuted, fontSize: 11, marginTop: 4 }}
                  data-testid="run-error"
                >
                  {r.error}
                </div>
              )}
            </td>
            <td style={{ padding: 8, color: theme.textSecondary }}>
              {timeAgo(r.started_at)}
            </td>
            <td className="num" style={{ padding: 8 }}>
              {r.duration_seconds == null ? "—" : `${r.duration_seconds.toFixed(1)}s`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RunHistory({ runs }: { runs: RunRecord[] }) {
  return (
    <section className="card card-pad" style={{ marginTop: 16, overflowX: "auto" }}>
      <h2 style={{ margin: "0 0 12px", fontSize: "var(--t-title)" }}>Run history</h2>
      {runs.length === 0 ? (
        <EmptyState
          title="No recent runs"
          hint="Trigger a run above, or wait for the daemon's next scheduled cycle."
        />
      ) : (
        <RunsTable runs={runs} />
      )}
    </section>
  );
}

/**
 * GET /runs/history — the daemon's durable pipeline_runs DB table (desktop/
 * run_history_store.py). Distinct from RunHistory above (which reflects
 * ControlStatus.run_history, an in-memory ring capped at 10 and lost on a
 * daemon restart): this table survives a restart, at the cost of only ever
 * showing terminal (succeeded/failed) runs — a run still in flight is never
 * written here, so it won't appear until it finishes. No auto-polling (this
 * isn't a "live" view); a manual refresh mirrors the honest, battery-minded
 * posture the rest of this screen already takes toward polling.
 */
function DurableRunHistory({
  runs,
  loading,
  error,
  httpStatus,
  onReload,
}: {
  runs: RunRecord[] | null;
  loading: boolean;
  error: string | null;
  httpStatus: number | null;
  onReload: () => void;
}) {
  return (
    <section className="card card-pad" style={{ marginTop: 16, overflowX: "auto" }}>
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 2px", fontSize: "var(--t-title)" }}>
            Full run history
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: 13, margin: 0 }}>
            Persisted to the database — survives a daemon restart.
          </p>
        </div>
        <Button
          onClick={onReload}
          disabled={loading}
          data-testid="refresh-run-history"
        >
          Refresh
        </Button>
      </div>

      {loading && !runs ? (
        <div style={{ marginTop: 12 }}>
          <Loading />
        </div>
      ) : error && !runs ? (
        <div style={{ marginTop: 12 }}>
          <ErrorState message={error} status={httpStatus} onRetry={onReload} />
        </div>
      ) : !runs || runs.length === 0 ? (
        <div style={{ marginTop: 12 }}>
          <EmptyState
            title="No persisted run history yet"
            hint="History is written once a triggered run finishes."
          />
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <RunsTable runs={runs} />
        </div>
      )}
    </section>
  );
}

export function PipelineDashboard() {
  const nav = useNavigate();
  const back = () => (window.history.length > 1 ? nav(-1) : nav("/"));

  const {
    data,
    loading,
    error,
    status: httpStatus,
    stale,
    cachedAt,
    reload,
  } = useApi<ControlStatus>(() => api.getControlStatus(), []);

  // Poll every 3s ONLY while a run is actually in flight — not a phone's radio
  // budget spent polling a status that changes once every few minutes.
  const inFlight = Boolean(data?.is_running || data?.current_run_id);
  usePoll(reload, 3000, inFlight);

  const {
    data: history,
    loading: historyLoading,
    error: historyError,
    status: historyStatus,
    reload: reloadHistory,
  } = useApi<RunRecord[]>(() => api.getRunHistory(50), []);

  // The durable table only ever gains a row once a run finishes (see
  // DurableRunHistory's docstring), so refetch it the moment `inFlight` flips
  // false->true->false — i.e. whenever a run this screen was watching just
  // completed — rather than making the caller hit "Refresh" manually.
  const wasInFlight = useRef(inFlight);
  useEffect(() => {
    if (wasInFlight.current && !inFlight) reloadHistory();
    wasInFlight.current = inFlight;
  }, [inFlight, reloadHistory]);

  return (
    <div className="screen" data-testid="pipeline-screen">
      <button
        onClick={back}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: theme.textSecondary,
          fontSize: 14,
        }}
      >
        ‹ Back
      </button>
      <h1 className="screen-title">Pipeline</h1>
      <p className="screen-sub">
        The orchestrator daemon's live status and stage-scoped run triggers.
      </p>

      <TabGuide tabKey="pipeline" />

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && !data ? (
        <Loading />
      ) : error && !data ? (
        <ErrorState message={error} status={httpStatus} onRetry={reload} />
      ) : data ? (
        <>
          <StatusBanner status={data} />
          <Controls disabled={data.is_running} onTriggered={reload} />
          <RunHistory runs={data.run_history} />
          <DurableRunHistory
            runs={history}
            loading={historyLoading}
            error={historyError}
            httpStatus={historyStatus}
            onReload={reloadHistory}
          />
        </>
      ) : null}
    </div>
  );
}
