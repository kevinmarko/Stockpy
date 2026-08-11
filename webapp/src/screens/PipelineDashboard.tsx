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
import { useNavigate } from "react-router";
import toast from "react-hot-toast";
import { DynamicGrid } from "../components/DynamicGrid";
import { api, ApiError } from "../api/client";
import type {
  ControlStatus,
  ControlStatusOnline,
  DeadLetterQueue,
  DeadLetterQueueEntry,
  RunRecord,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { useMutation } from "../hooks/useMutation";
import {
  Button,
  EmptyState,
  ErrorState,
  Loading,
  Notice,
  StaleDataNotice,
  Table,
} from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { timeAgo } from "../format";
import { theme } from "../theme";

type TriggerKind = "full" | "data" | "metrics";

const TRIGGER_LABELS: Record<TriggerKind, string> = {
  full: "Full advisory pipeline",
  data: "Data refresh",
  metrics: "Metrics refresh",
};

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

function StatusBanner({ status }: { status: ControlStatusOnline }) {
  const running = status.is_running;
  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)", padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
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

      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
        {running && status.current_run_id && (
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", margin: "0 0 var(--s-3)" }}>
            Current run:{" "}
            <span className="num" style={{ fontFamily: "monospace" }}>
              {status.current_run_id}
            </span>
          </p>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)" }}>
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
          <Notice variant="warn" style={{ marginTop: "var(--s-3)" }}>
            <span aria-hidden>⚠️</span>
            <span>
              Kill switch active
              {status.kill_switch_reason ? `: ${status.kill_switch_reason}` : ""}. New
              runs are paused.
            </span>
          </Notice>
        )}
      </div>
    </section>
  );
}

/**
 * The honest render for `{"daemon_alive": false}` — no `OrchestratorDaemon`
 * is currently attached to the Control API process (startup window, mid
 * restart, or the API served standalone). Every other `ControlStatus` field
 * is genuinely absent from that response, not merely null, so this renders
 * in place of StatusBanner/Controls/RunHistory rather than passing partial
 * data into components that expect the full `ControlStatusOnline` shape.
 */
function DaemonOfflineNotice() {
  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)", padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <span
          aria-hidden
          style={{ width: 12, height: 12, borderRadius: "50%", flex: "0 0 auto", background: theme.textMuted }}
        />
        <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>Daemon offline</div>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
        <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", margin: "0" }}>
          No orchestrator daemon is attached to the Control API right now, so
          live status, run triggers, and run history are unavailable until it
          reconnects.
        </p>
      </div>
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
  // useMutation's `run` swallows the thrown error into `error` state (see
  // useMutation.ts) rather than rethrowing or returning it -- and reading
  // `trigger.error` back inside this async `handle` closure would be stale
  // (it's a snapshot from the render `handle` was created in, not the render
  // the mutation's own setState calls produced). A ref sidesteps that: it's
  // written synchronously in the same tick the underlying promise rejects,
  // ahead of useMutation's own catch, so it's always fresh by the time
  // `await trigger.run(kind)` resolves.
  const lastErrorRef = useRef<string | null>(null);
  const trigger = useMutation((kind: TriggerKind) => {
    const call =
      kind === "data"
        ? triggerControl(() => api.postControlPipelineData())
        : kind === "metrics"
          ? triggerControl(() => api.postControlPipelineMetrics())
          : triggerControl(() => api.postControlRun());
    return call.catch((e) => {
      lastErrorRef.current = e instanceof Error ? e.message : String(e);
      throw e;
    });
  });

  const handle = async (kind: TriggerKind) => {
    setPendingKind(kind);
    lastErrorRef.current = null;
    const res = await trigger.run(kind);
    setPendingKind(null);
    if (res) {
      toast.success(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>{TRIGGER_LABELS[kind]} triggered</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {res.state ?? "queued"}{res.run_id ? ` — ${res.run_id}` : ""}
          </span>
        </div>
      );
    } else {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>{TRIGGER_LABELS[kind]} failed to trigger</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {lastErrorRef.current ?? "Request failed."}
          </span>
        </div>
      );
    }
    onTriggered();
  };

  const busy = disabled || trigger.pending;

  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <h2 style={{ margin: "0", fontSize: "var(--t-title)" }}>Trigger a run</h2>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", marginTop: 0, marginBottom: "var(--s-3)" }}>
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

      <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-2-5)" }}>
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
        <Notice variant="warn" style={{ marginTop: "var(--s-2-5)" }}>
          <span aria-hidden>⚠️</span>
          <span>{trigger.error}</span>
        </Notice>
      )}
      {trigger.result && (
        <Notice variant="success" style={{ marginTop: "var(--s-2-5)" }}>
          <span aria-hidden>✅</span>
          <span>
            {trigger.result.state ?? "queued"}
            {trigger.result.run_id ? ` — ${trigger.result.run_id}` : ""}
            {"mode" in trigger.result && trigger.result.mode
              ? ` (${trigger.result.mode})`
              : ""}
            .
          </span>
        </Notice>
      )}
      </div>
    </section>
  );
}

function RunsTable({ runs }: { runs: RunRecord[] }) {
  return (
    <Table>
      <thead>
        <tr>
          <th>Run</th>
          <th>Mode</th>
          <th>State</th>
          <th>Started</th>
          <th className="num">Duration</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.run_id}>
            <td style={{ fontFamily: "monospace", fontSize: "var(--t-caption)" }}>
              {r.run_id}
            </td>
            <td>
              {r.mode ? (
                <span className="chip">{r.mode.toUpperCase()}</span>
              ) : (
                <span style={{ color: theme.textMuted }}>—</span>
              )}
            </td>
            <td>
              <StateBadge state={r.state} />
              {r.error && (
                <div
                  style={{ color: theme.textMuted, fontSize: "var(--t-micro)", marginTop: "var(--s-1)" }}
                  data-testid="run-error"
                >
                  {r.error}
                </div>
              )}
            </td>
            <td style={{ color: theme.textSecondary }}>
              {timeAgo(r.started_at)}
            </td>
            <td className="num">
              {r.duration_seconds == null ? "—" : `${r.duration_seconds.toFixed(1)}s`}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function RunHistory({ runs }: { runs: RunRecord[] }) {
  return (
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <h2 style={{ margin: "0", fontSize: "var(--t-title)" }}>Run history</h2>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflowX: "auto" }}>
        {runs.length === 0 ? (
          <EmptyState
            title="No recent runs"
            hint="Trigger a run above, or wait for the daemon's next scheduled cycle."
          />
        ) : (
          <RunsTable runs={runs} />
        )}
      </div>
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
    <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div
        className="drag-handle"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--s-2)",
          padding: "var(--s-3)",
          borderBottom: `1px solid rgba(255, 255, 255, 0.08)`,
          cursor: "grab",
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 var(--s-0-5)", fontSize: "var(--t-title)" }}>
            Full run history
          </h2>
          <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", margin: 0 }}>
            Persisted to the database — survives a daemon restart.
          </p>
        </div>
        <Button
          onClick={onReload}
          disabled={loading}
          data-testid="refresh-run-history"
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
        >
          Refresh
        </Button>
      </div>

      <div style={{ padding: "var(--s-3)", flex: 1, overflowX: "auto" }}>
        {loading && !runs ? (
        <div style={{ marginTop: "var(--s-3)" }}>
          <Loading />
        </div>
      ) : error && !runs ? (
        <div style={{ marginTop: "var(--s-3)" }}>
          <ErrorState message={error} status={httpStatus} onRetry={onReload} />
        </div>
      ) : !runs || runs.length === 0 ? (
        <div style={{ marginTop: "var(--s-3)" }}>
          <EmptyState
            title="No persisted run history yet"
            hint="History is written once a triggered run finishes."
          />
        </div>
      ) : (
        <RunsTable runs={runs} />
      )}
      </div>
    </section>
  );
}

/**
 * DeadLetterQueueSection — Streamlit Launcher tab's dead-letter queue
 * (gui/panels/launcher.py::_render_dead_letter_queue) ported to the webapp.
 * Backed by GET /dead-letter (fail-open read) + POST /dead-letter/retry
 * (fail-closed command token + the dedicated DEAD_LETTER_RETRY_ENABLED
 * flag). `is_clean: null` (no run yet) is rendered distinctly from
 * `is_clean: true` (a genuinely clean last run) — CONSTRAINT #4, "no run
 * yet" is not the same claim as "the last run was clean". Retry does not
 * stream logs the way Console.tsx's job launchers do (this is a bespoke
 * subprocess spawn, not routed through the generic POST /jobs job manager) —
 * it reports the spawned PID/log path it was actually given, honestly.
 */
function DeadLetterRow({
  entry,
  retryEnabled,
}: {
  entry: DeadLetterQueueEntry;
  retryEnabled: boolean;
}) {
  // Same "ref captures the fresh error, ahead of useMutation's own catch"
  // reasoning as Controls.handle above.
  const lastErrorRef = useRef<string | null>(null);
  const retry = useMutation(() =>
    api.retryDeadLetter(entry.symbol).catch((e) => {
      lastErrorRef.current = e instanceof Error ? e.message : String(e);
      throw e;
    })
  );

  const handleRetry = async () => {
    lastErrorRef.current = null;
    const res = await retry.run();
    if (res) {
      toast.success(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Retry queued for {entry.symbol}</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {res.note} (PID {res.pid})
          </span>
        </div>
      );
    } else {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Retry failed for {entry.symbol}</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {lastErrorRef.current ?? "Request failed."}
          </span>
        </div>
      );
    }
  };

  return (
    <div
      className="card card-pad"
      style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}
      data-testid={`dead-letter-row-${entry.symbol}`}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--s-2)", flexWrap: "wrap" }}>
        <div>
          <span style={{ fontFamily: "monospace", fontWeight: 700 }}>{entry.symbol}</span>
          <span style={{ color: theme.textMuted, fontSize: "var(--t-caption)", marginLeft: "var(--s-2)" }}>
            stage: {entry.stage}
          </span>
        </div>
        <Button
          variant="neutral"
          disabled={!retryEnabled}
          pending={retry.pending}
          onClick={() => void handleRetry()}
          data-testid={`retry-${entry.symbol}`}
        >
          🔄 Retry
        </Button>
      </div>
      <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{entry.error}</div>
      {retry.error && (
        <Notice variant="warn">
          <span aria-hidden>⚠️</span>
          <span>{retry.error}</span>
        </Notice>
      )}
      {retry.result && (
        <Notice variant="success" data-testid={`retry-result-${entry.symbol}`}>
          <span aria-hidden>✅</span>
          <span>
            {retry.result.note} (PID {retry.result.pid}, log: {retry.result.log_path})
          </span>
        </Notice>
      )}
    </div>
  );
}

function DeadLetterQueueSection() {
  const { data, loading, error, status, reload } = useApi<DeadLetterQueue>(
    () => api.getDeadLetter(),
    []
  );

  return (
    <section className="card card-pad" data-testid="dead-letter-section" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
      <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)`, cursor: "grab" }}>
        <h2 style={{ margin: "0", fontSize: "var(--t-title)" }}>Dead-letter queue</h2>
      </div>
      <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
      <p style={{ color: theme.textSecondary, fontSize: "var(--t-body)", margin: "0 0 var(--s-3)" }}>
        Symbols that failed during the last pipeline run. Each failure is
        isolated — the rest of the run was unaffected.
      </p>

      {loading && <Loading lines={2} />}
      {!loading && error && <ErrorState message={error} status={status} onRetry={reload} />}
      {!loading && !error && data && (
        data.is_clean == null ? (
          <p style={{ color: theme.textMuted, fontSize: "var(--t-body)" }}>
            {data.reason ?? "No dead-letter report yet — run the pipeline once to populate it."}
          </p>
        ) : data.is_clean ? (
          <Notice variant="success">
            <span aria-hidden>✅</span>
            <span>
              All symbols processed cleanly in the last run
              {data.run_id ? ` (${data.run_id.slice(0, 19)})` : ""}.
            </span>
          </Notice>
        ) : (
          <>
            <Notice variant="warn" style={{ marginBottom: "var(--s-3)" }}>
              <span aria-hidden>⚠️</span>
              <span>
                {data.entries.length} symbol(s) failed in the last run
                {data.run_id ? ` (${data.run_id.slice(0, 19)})` : ""}. Use Retry to
                re-evaluate a single symbol.
              </span>
            </Notice>
            {!data.retry_enabled && (
              <Notice variant="info" style={{ marginBottom: "var(--s-3)" }}>
                <span aria-hidden>ℹ️</span>
                <span>Retry is disabled on the server (DEAD_LETTER_RETRY_ENABLED=false).</span>
              </Notice>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
              {data.entries.map((entry) => (
                <DeadLetterRow key={entry.symbol} entry={entry} retryEnabled={data.retry_enabled} />
              ))}
            </div>
          </>
        )
      )}
      </div>
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
  // budget spent polling a status that changes once every few minutes. Guard
  // on daemon_alive first: a bare `{daemon_alive: false}` response has none
  // of the other fields, so short-circuit before ever reading them.
  const inFlight = Boolean(
    data?.daemon_alive && (data.is_running || data.current_run_id)
  );
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
          fontSize: "var(--t-callout)",
        }}
      >
        ‹ Back
      </button>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>Pipeline</h1>
          <p className="screen-sub">
            The orchestrator daemon's live status and stage-scoped run triggers.
          </p>
        </div>
      </div>

      <TabGuide tabKey="pipeline" />

      {stale && <StaleDataNotice cachedAt={cachedAt} onRetry={reload} />}

      {loading && !data ? (
        <Loading />
      ) : error && !data ? (
        <ErrorState message={error} status={httpStatus} onRetry={reload} />
      ) : data ? (
        <div style={{ flex: 1, minHeight: 0 }}>
          <DynamicGrid
            layoutKey="pipeline"
            defaultLayouts={{
              lg: [
                { i: "status", x: 0, y: 0, w: 6, h: 2, minW: 4, minH: 2 },
                { i: "controls", x: 6, y: 0, w: 6, h: 2, minW: 4, minH: 2 },
                { i: "history", x: 0, y: 2, w: 6, h: 4, minW: 4, minH: 3 },
                { i: "durableHistory", x: 6, y: 2, w: 6, h: 4, minW: 4, minH: 3 },
                { i: "deadLetter", x: 0, y: 6, w: 12, h: 4, minW: 6, minH: 3 },
              ],
            }}
          >
            {data.daemon_alive ? (
              <div key="status"><StatusBanner status={data} /></div>
            ) : (
              <div key="status"><DaemonOfflineNotice /></div>
            )}
            {data.daemon_alive && <div key="controls"><Controls disabled={data.is_running} onTriggered={reload} /></div>}
            {data.daemon_alive && <div key="history"><RunHistory runs={data.run_history} /></div>}

            <div key="durableHistory">
              <DurableRunHistory
                runs={history}
                loading={historyLoading}
                error={historyError}
                httpStatus={historyStatus}
                onReload={reloadHistory}
              />
            </div>
            
            <div key="deadLetter">
              <DeadLetterQueueSection />
            </div>
          </DynamicGrid>
        </div>
      ) : null}
    </div>
  );
}
