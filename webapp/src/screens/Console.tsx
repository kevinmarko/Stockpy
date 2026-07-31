import React, { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { JobRecord } from "../api/types";
import { LogStream } from "../components/LogStream";
import { TabGuide } from "../components/TabGuide";
import { Button } from "../components/ui";
import { theme } from "../theme";

const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);
const STATUS_POLL_MS = 1500;

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
  onClick: () => void;
}

export const Console: React.FC = () => {
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [showBacktestForm, setShowBacktestForm] = useState(false);
  const [backtestStrategies, setBacktestStrategies] = useState("");
  const [backtestStart, setBacktestStart] = useState(yearAgoIso());
  const [backtestEnd, setBacktestEnd] = useState(todayIso());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // Poll GET /jobs/{id} while the job is in flight so the status badge
  // actually reflects reality (the SSE `end` event only tells LogStream to
  // stop reading — it doesn't tell this component anything).
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
    } catch (err: any) {
      alert(`Failed to launch job: ${err.message || err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleRunBacktest = async () => {
    const strategies = backtestStrategies
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (strategies.length === 0) {
      alert("Enter at least one strategy id (comma-separated).");
      return;
    }
    if (!backtestStart || !backtestEnd) {
      alert("Start and end dates are required.");
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
      } else {
        alert("Cancel was requested but could not be confirmed — the job may still be running.");
      }
    } catch (err: any) {
      alert(`Failed to cancel job: ${err.message || err}`);
    }
  };

  const actions: QuickAction[] = [
    {
      key: "preflight",
      icon: "🛡️",
      label: "Preflight Check",
      description: "Validate environment & keys",
      onClick: () => handleLaunch("preflight"),
    },
    {
      key: "pytest",
      icon: "🧪",
      label: "Run Test Suite",
      description: "Execute full pytest suite",
      onClick: () => handleLaunch("pytest"),
    },
    {
      key: "advisory",
      icon: "🚀",
      label: "Advisory Pipeline",
      description: "Run main.py cycle",
      onClick: () => handleLaunch("advisory"),
    },
    {
      key: "verify",
      icon: "⚡",
      label: "Full Verification",
      description: "Env + Tests + Live Cycle",
      onClick: () => handleLaunch("verify"),
    },
    {
      key: "gravity",
      icon: "🔍",
      label: "Gravity Audit",
      description: "Run Gravity AI Review Suite",
      onClick: () => handleLaunch("gravity"),
    },
    {
      key: "backtest",
      icon: "📊",
      label: "Run Backtest",
      description: "Validation harness (PBO/DSR/Sharpe)",
      onClick: () => setShowBacktestForm((v) => !v),
    },
  ];

  return (
    <div className="screen">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "var(--s-3)" }}>
        <div>
          <h1 className="screen-title">One-Click Command Center</h1>
          <p className="screen-sub" style={{ marginBottom: 0 }}>
            Launch background execution tasks &amp; monitor real-time logs.
          </p>
        </div>
        {activeJob && activeJob.cancellable && activeJob.is_running !== false && (
          <Button variant="neutral" onClick={handleCancel} data-testid="console-cancel-job">
            Cancel Active Job
          </Button>
        )}
      </div>

      <TabGuide tabKey="console" />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: "var(--s-3)",
          marginTop: "var(--s-4)",
        }}
      >
        {actions.map((a) => (
          <button
            key={a.key}
            type="button"
            disabled={loading}
            onClick={a.onClick}
            className="card card-pad"
            style={{
              textAlign: "left",
              cursor: loading ? "not-allowed" : "pointer",
              opacity: loading ? 0.6 : 1,
            }}
          >
            <div style={{ fontWeight: 700, fontSize: "var(--t-callout)", color: theme.textPrimary, marginBottom: "var(--s-1)" }}>
              {a.icon} {a.label}
            </div>
            <div style={{ color: theme.textMuted, fontSize: "var(--t-caption)" }}>{a.description}</div>
          </button>
        ))}
      </div>

      {showBacktestForm && (
        <section className="card card-pad" style={{ marginTop: "var(--s-4)" }}>
          <div style={{ fontWeight: 700, fontSize: "var(--t-callout)", color: theme.textPrimary, marginBottom: "var(--s-3)" }}>
            Run Backtest
          </div>
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
              <input
                type="date"
                className="input"
                value={backtestStart}
                onChange={(e) => setBacktestStart(e.target.value)}
              />
            </div>
            <div>
              <label className="tile-label" style={{ display: "block", marginBottom: "var(--s-1-5)" }}>
                End date
              </label>
              <input
                type="date"
                className="input"
                value={backtestEnd}
                onChange={(e) => setBacktestEnd(e.target.value)}
              />
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
        </section>
      )}

      {activeJob && (
        <section
          className="card card-pad"
          style={{
            marginTop: "var(--s-4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: "var(--s-2)",
          }}
        >
          <div>
            <span style={{ fontSize: "var(--t-caption)", fontWeight: 700, color: theme.textMuted, textTransform: "uppercase", letterSpacing: "0.04em" }}>
              Active Job:{" "}
            </span>
            <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", color: theme.textPrimary, marginLeft: "var(--s-1)" }}>
              {activeJob.job_id} ({activeJob.job_type})
            </span>
          </div>
          <span className="badge badge-neutral">{activeJob.status}</span>
        </section>
      )}

      <div style={{ marginTop: "var(--s-4)" }}>
        <LogStream jobId={activeJob?.job_id} isStreaming={Boolean(activeJob)} />
      </div>
    </div>
  );
};
