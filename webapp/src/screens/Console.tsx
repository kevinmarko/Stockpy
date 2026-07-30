import React, { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { JobRecord } from "../api/types";
import { LogStream } from "../components/LogStream";

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

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">One-Click Command Center</h1>
          <p className="text-sm text-zinc-400">Launch background execution tasks & monitor real-time logs</p>
        </div>
        {activeJob && activeJob.cancellable && activeJob.is_running !== false && (
          <button
            onClick={handleCancel}
            className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white font-medium text-sm rounded shadow transition-colors"
          >
            Cancel Active Job
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <button
          disabled={loading}
          onClick={() => handleLaunch("preflight")}
          className="p-4 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-all"
        >
          <div className="font-semibold text-zinc-200 mb-1">🛡️ Preflight Check</div>
          <div className="text-xs text-zinc-400">Validate environment & keys</div>
        </button>

        <button
          disabled={loading}
          onClick={() => handleLaunch("pytest")}
          className="p-4 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-all"
        >
          <div className="font-semibold text-zinc-200 mb-1">🧪 Run Test Suite</div>
          <div className="text-xs text-zinc-400">Execute full pytest suite</div>
        </button>

        <button
          disabled={loading}
          onClick={() => handleLaunch("advisory")}
          className="p-4 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-all"
        >
          <div className="font-semibold text-zinc-200 mb-1">🚀 Advisory Pipeline</div>
          <div className="text-xs text-zinc-400">Run main.py cycle</div>
        </button>

        <button
          disabled={loading}
          onClick={() => handleLaunch("verify")}
          className="p-4 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-all"
        >
          <div className="font-semibold text-zinc-200 mb-1">⚡ Full Verification</div>
          <div className="text-xs text-zinc-400">Env + Tests + Live Cycle</div>
        </button>

        <button
          disabled={loading}
          onClick={() => handleLaunch("gravity")}
          className="p-4 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-all"
        >
          <div className="font-semibold text-zinc-200 mb-1">🔍 Gravity Audit</div>
          <div className="text-xs text-zinc-400">Run Gravity AI Review Suite</div>
        </button>

        <button
          disabled={loading}
          onClick={() => setShowBacktestForm((v) => !v)}
          className="p-4 bg-zinc-900 border border-zinc-800 hover:border-zinc-700 rounded-lg text-left transition-all"
        >
          <div className="font-semibold text-zinc-200 mb-1">📊 Run Backtest</div>
          <div className="text-xs text-zinc-400">Validation harness (PBO/DSR/Sharpe)</div>
        </button>
      </div>

      {showBacktestForm && (
        <div className="bg-zinc-900 border border-zinc-800 p-4 rounded-lg space-y-3">
          <div className="font-semibold text-zinc-200">Run Backtest</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="text-xs text-zinc-400 space-y-1">
              <div>Strategies (comma-separated ids)</div>
              <input
                type="text"
                value={backtestStrategies}
                onChange={(e) => setBacktestStrategies(e.target.value)}
                placeholder="rsi2_mean_reversion, macd_trend"
                className="w-full bg-zinc-950 border border-zinc-700 text-sm text-zinc-200 px-2 py-1.5 rounded focus:outline-none focus:border-zinc-500"
              />
            </label>
            <label className="text-xs text-zinc-400 space-y-1">
              <div>Start date</div>
              <input
                type="date"
                value={backtestStart}
                onChange={(e) => setBacktestStart(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-700 text-sm text-zinc-200 px-2 py-1.5 rounded focus:outline-none focus:border-zinc-500"
              />
            </label>
            <label className="text-xs text-zinc-400 space-y-1">
              <div>End date</div>
              <input
                type="date"
                value={backtestEnd}
                onChange={(e) => setBacktestEnd(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-700 text-sm text-zinc-200 px-2 py-1.5 rounded focus:outline-none focus:border-zinc-500"
              />
            </label>
          </div>
          <div className="flex gap-2">
            <button
              disabled={loading}
              onClick={handleRunBacktest}
              className="px-4 py-2 bg-emerald-700 hover:bg-emerald-600 text-white font-medium text-sm rounded transition-colors"
            >
              Run Backtest
            </button>
            <button
              onClick={() => setShowBacktestForm(false)}
              className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 font-medium text-sm rounded transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {activeJob && (
        <div className="bg-zinc-900 border border-zinc-800 p-4 rounded-lg flex items-center justify-between">
          <div>
            <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Active Job: </span>
            <span className="text-sm font-mono text-zinc-200 ml-2">{activeJob.job_id} ({activeJob.job_type})</span>
          </div>
          <div>
            <span className="px-2 py-1 text-xs rounded font-medium bg-blue-900 text-blue-200">
              {activeJob.status}
            </span>
          </div>
        </div>
      )}

      <LogStream jobId={activeJob?.job_id} isStreaming={Boolean(activeJob)} />
    </div>
  );
};
