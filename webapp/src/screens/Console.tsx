import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { TabGuide } from "../components/TabGuide";
import { LogStream } from "../components/LogStream";
import { DataTable, type Column } from "../components/DataTable";
import { Button } from "../components/ui";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useAutoPoll } from "../hooks/useAutoPoll";
import { useAutoRefresh } from "../components/AutoRefreshContext";
import type { JobRecord, ObservabilitySummary } from "../api/types";
import { theme } from "../theme";
import { timeAgo } from "../format";

const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);
const STATUS_POLL_MS = 1500;
const TELEMETRY_POLL_MS = 10_000;

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function yearAgoIso(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

interface QuickAction {
  key: string;
  icon: string;
  label: string;
  description: string;
  jobType: string;
  params?: Record<string, unknown>;
}

const QUICK_ACTIONS: QuickAction[] = [
  { key: "preflight", icon: "🛡️", label: "Preflight Check", description: "Validate environment & keys", jobType: "preflight" },
  { key: "pytest", icon: "🧪", label: "Run Test Suite", description: "Execute full pytest suite", jobType: "pytest" },
  { key: "advisory", icon: "🚀", label: "Advisory Pipeline", description: "Run main.py cycle", jobType: "advisory" },
  { key: "verify", icon: "⚡", label: "Full Verification", description: "Env + Tests + Live Cycle", jobType: "verify" },
  { key: "gravity", icon: "🔍", label: "Gravity Audit", description: "Run Gravity AI Review Suite", jobType: "gravity" },
];

function fmtPercent(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)}%`;
}

function fmtBytesShort(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(0)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/** Real host + this-API-process telemetry (psutil-backed). Not a per-job
 *  process manager -- the backend exposes no such surface (and none should
 *  be added: killing arbitrary host PIDs from a browser is the same
 *  RCE-adjacent risk this codebase's cron/systemd write paths deliberately
 *  stayed read-only for). */
function SystemResourcesPanel() {
  const telemetry = useApi<ObservabilitySummary>(() => api.getObservabilitySummary("1W", 10), []);
  const { autoRefreshIntervalMs } = useAutoRefresh();
  // TELEMETRY_POLL_MS is a FLOOR, not a target: this is a heavier composite
  // read that must never poll faster than its original cadence even if the
  // operator picks a short global interval.
  useAutoPoll(telemetry.reload, "observability", {
    hasError: telemetry.error != null,
    customIntervalMs: Math.max(TELEMETRY_POLL_MS, autoRefreshIntervalMs),
  });
  const t = telemetry.data?.system_telemetry;

  if (telemetry.loading && !t) {
    return <div className="empty" style={{ padding: "var(--s-4)" }}>Loading system telemetry…</div>;
  }
  if (!t || !t.psutil_available) {
    return (
      <div className="empty" style={{ padding: "var(--s-4)" }}>
        {t?.reason ?? "System telemetry unavailable (psutil not installed on the backend host)."}
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "var(--s-3)" }}>
      <ResourceTile label="Host CPU" value={fmtPercent(t.cpu_percent)} sub={t.cpu_count_logical ? `${t.cpu_count_logical} logical cores` : undefined} />
      <ResourceTile label="Load Avg (1m)" value={t.load_avg_1m == null ? "—" : t.load_avg_1m.toFixed(2)} />
      <ResourceTile label="Host Memory" value={fmtPercent(t.memory_percent)} sub={t.memory_used_bytes != null && t.memory_total_bytes != null ? `${fmtBytesShort(t.memory_used_bytes)} / ${fmtBytesShort(t.memory_total_bytes)}` : undefined} />
      <ResourceTile label="Host Disk" value={fmtPercent(t.disk_percent)} sub={t.disk_used_bytes != null && t.disk_total_bytes != null ? `${fmtBytesShort(t.disk_used_bytes)} / ${fmtBytesShort(t.disk_total_bytes)}` : undefined} />
      <ResourceTile label="This API Process — RSS" value={fmtBytesShort(t.process_rss_bytes)} />
      <ResourceTile label="This API Process — CPU" value={fmtPercent(t.process_cpu_percent)} sub={t.process_threads != null ? `${t.process_threads} threads` : undefined} />
      {t.sampled_at && (
        <div style={{ gridColumn: "1 / -1", color: theme.textMuted, fontSize: "var(--t-caption)" }}>
          Sampled {timeAgo(t.sampled_at)}
        </div>
      )}
    </div>
  );
}

function ResourceTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{ background: "var(--surface-2)", padding: "var(--s-3)", borderRadius: "var(--r-sm)" }}>
      <div style={{ fontSize: "var(--t-micro)", color: "var(--text-muted)", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "var(--t-title)", fontWeight: 700, color: "var(--text-primary)", marginTop: "2px" }}>{value}</div>
      {sub && <div style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)", marginTop: "2px" }}>{sub}</div>}
    </div>
  );
}

const JOB_COLUMNS: Column<JobRecord>[] = [
  { key: "job_id", header: "Job ID" },
  { key: "job_type", header: "Type" },
  {
    key: "status",
    header: "Status",
    render: (row) => (
      <span
        className="badge"
        style={{
          background: row.status === "running" ? "rgba(56, 189, 248, 0.15)" : row.status === "success" ? "rgba(16, 185, 129, 0.15)" : row.status === "failed" ? "rgba(239, 68, 68, 0.15)" : undefined,
        }}
      >
        {row.status}
      </span>
    ),
  },
  { key: "created_at", header: "Launched", render: (row) => (row.created_at ? timeAgo(row.created_at) : "—") },
  { key: "cancellable", header: "Cancellable", render: (row) => (row.status === "running" || row.is_running ? (row.cancellable ? "Yes" : "No") : "—") },
];

export function Console() {
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [jobHistory, setJobHistory] = useState<JobRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [showBacktestForm, setShowBacktestForm] = useState(false);
  const [backtestStrategies, setBacktestStrategies] = useState("");
  const [backtestStart, setBacktestStart] = useState(yearAgoIso());
  const [backtestEnd, setBacktestEnd] = useState(todayIso());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const recordJob = (job: JobRecord) => {
    setJobHistory((prev) => {
      const idx = prev.findIndex((j) => j.job_id === job.job_id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = job;
        return next;
      }
      return [job, ...prev];
    });
  };

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // Poll GET /jobs/{id} while the job is in flight so the status badge
  // actually reflects reality (the SSE `end` event only tells LogStream to
  // stop reading -- it doesn't tell this component anything).
  useEffect(() => {
    if (!activeJob || TERMINAL_STATUSES.has(activeJob.status)) {
      stopPolling();
      return;
    }
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const latest = await api.getJobStatus(activeJob.job_id);
        setActiveJob(latest);
        recordJob(latest);
      } catch {
        // A transient poll failure isn't fatal -- just try again next tick.
      }
    }, STATUS_POLL_MS);
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeJob?.job_id, activeJob?.status]);

  useEffect(() => stopPolling, []);

  const handleLaunch = async (jobType: string, params?: Record<string, unknown>) => {
    try {
      setLoading(true);
      const res = await api.createJob(jobType, params);
      setActiveJob(res);
      recordJob(res);
      toast.success(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Job {jobType} started</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            ID: {res.job_id}
          </span>
        </div>
      );
    } catch (err: any) {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Job {jobType} failed to launch</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {err?.message ?? String(err)}
          </span>
        </div>
      );
    } finally {
      setLoading(false);
    }
  };

  const handleRunBacktest = async () => {
    const strategies = backtestStrategies.split(",").map((s) => s.trim()).filter(Boolean);
    if (strategies.length === 0) {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Enter at least one strategy id (comma-separated).</span>
        </div>
      );
      return;
    }
    if (!backtestStart || !backtestEnd) {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Start and end dates are required.</span>
        </div>
      );
      return;
    }
    await handleLaunch("validation", { strategies, start: backtestStart, end: backtestEnd });
    setShowBacktestForm(false);
  };

  const handleCancel = async () => {
    if (!activeJob) return;
    try {
      const res = await api.cancelJob(activeJob.job_id);
      if (res.cancelled) {
        const latest = await api.getJobStatus(activeJob.job_id);
        setActiveJob(latest);
        recordJob(latest);
        toast(
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Job cancelled</span>
            <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
              {activeJob.job_id} cancelled.
            </span>
          </div>,
          { icon: '⚠️' }
        );
      } else {
        toast(
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Cancel requested</span>
            <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
              Could not be confirmed — the job may still be running.
            </span>
          </div>,
          { icon: '⚠️' }
        );
      }
    } catch (err: any) {
      toast.error(
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: 'var(--t-callout)' }}>Cancel failed</span>
          <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--t-caption)', marginTop: '4px' }}>
            {err?.message ?? String(err)}
          </span>
        </div>
      );
    }
  };

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <h1 className="screen-title" style={{ marginTop: "var(--s-2)" }}>One-Click Command Center</h1>
          <p className="screen-sub" style={{ marginBottom: 0 }}>
            Launch background execution tasks, monitor real-time logs, and watch host resource usage.
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--s-2)" }}>
        {activeJob && activeJob.cancellable && activeJob.is_running !== false && (
          <Button variant="neutral" onClick={handleCancel} data-testid="console-cancel-job">
            Cancel Active Job
          </Button>
        )}
        </div>
      </div>

      <TabGuide tabKey="console" />

      <div style={{ flex: 1, minHeight: 0, marginTop: "var(--s-4)" }}>
        <div className="dashboard-layout" style={{ display: "flex", flexDirection: "column", gap: "var(--s-4)" }}>
          <div key="quickLaunchers">
            {/* Quick Launchers */}
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Quick Launchers</h2>
              </div>
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "var(--s-2-5)" }}>
          {QUICK_ACTIONS.map((a) => (
            <button
              key={a.key}
              type="button"
              disabled={loading}
              onClick={() => handleLaunch(a.jobType, a.params)}
              className="card card-pad"
              style={{ textAlign: "left", cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.6 : 1 }}
            >
              <div style={{ fontWeight: 700, fontSize: "var(--t-callout)", color: theme.textPrimary, marginBottom: "var(--s-1)" }}>
                {a.icon} {a.label}
              </div>
              <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{a.description}</div>
            </button>
          ))}
          <button
            type="button"
            onClick={() => setShowBacktestForm((v) => !v)}
            className="card card-pad"
            style={{ textAlign: "left", cursor: "pointer" }}
          >
            <div style={{ fontWeight: 700, fontSize: "var(--t-callout)", color: theme.textPrimary, marginBottom: "var(--s-1)" }}>
              📊 Run Backtest
            </div>
            <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>Validation harness (PBO/DSR/Sharpe)</div>
          </button>
        </div>

        {showBacktestForm && (
          <div style={{ marginTop: "var(--s-3)", padding: "var(--s-3)", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--r-sm)" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "var(--s-3)" }}>
              <div>
                <label className="tile-label" style={{ display: "block", marginBottom: "var(--s-1-5)" }}>
                  Strategies (comma-separated ids)
                </label>
                <input
                  type="text"
                  className="input"
                  value={backtestStrategies}
                  onChange={(e) => setBacktestStrategies(e.target.value)}
                  placeholder="rsi2_mean_reversion, macd_trend"
                />
              </div>
              <div>
                <label className="tile-label" style={{ display: "block", marginBottom: "var(--s-1-5)" }}>
                  Start date
                </label>
                <input type="date" className="input" value={backtestStart} onChange={(e) => setBacktestStart(e.target.value)} />
              </div>
              <div>
                <label className="tile-label" style={{ display: "block", marginBottom: "var(--s-1-5)" }}>
                  End date
                </label>
                <input type="date" className="input" value={backtestEnd} onChange={(e) => setBacktestEnd(e.target.value)} />
              </div>
            </div>
            <div style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-3)" }}>
              <Button variant="primary" disabled={loading} onClick={handleRunBacktest}>
                Run Backtest
              </Button>
              <Button variant="neutral" onClick={() => setShowBacktestForm(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}

                {activeJob && (
                  <div style={{ marginTop: "var(--s-3)", padding: "var(--s-3)", background: "var(--surface-2)", borderRadius: "var(--r-sm)", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "var(--s-2)" }}>
                    <div>
                      <span style={{ fontSize: "var(--t-caption)", color: "var(--text-muted)" }}>Active Job: </span>
                      <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: "var(--accent)" }}>
                        {activeJob.job_id}
                      </span>{" "}
                      <span style={{ color: "var(--text-muted)" }}>({activeJob.job_type})</span>
                    </div>
                    <span className="badge badge-neutral">{activeJob.status}</span>
                  </div>
                )}
              </div>
            </section>
          </div>

          <div key="systemResources">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>System Resources</h2>
              </div>
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                <SystemResourcesPanel />
              </div>
            </section>
          </div>

          <div key="logStream">
            <LogStream jobId={activeJob?.job_id} isStreaming={Boolean(activeJob)} />
          </div>

          <div key="jobHistory">
            <section className="card card-pad" style={{ display: "flex", flexDirection: "column", height: "100%", padding: 0 }}>
              <div className="drag-handle" style={{ padding: "var(--s-3)", borderBottom: `1px solid rgba(255, 255, 255, 0.08)` }}>
                <h2 style={{ fontSize: "var(--t-subhead)", margin: 0 }}>Jobs launched this session</h2>
              </div>
              <div style={{ padding: "var(--s-3)", flex: 1, overflow: "auto" }}>
                {jobHistory.length > 0 ? (
                  <DataTable data={jobHistory} columns={JOB_COLUMNS} groupByKey="job_type" />
                ) : (
                  <div className="empty" style={{ padding: "var(--s-4)" }}>No jobs launched in this session.</div>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
