import React, { useState } from "react";
import { api } from "../api/client";
import { JobRecord } from "../api/types";
import { LogStream } from "../components/LogStream";

export const Console: React.FC = () => {
  const [activeJob, setActiveJob] = useState<JobRecord | null>(null);
  const [loading, setLoading] = useState(false);

  const handleLaunch = async (jobType: string) => {
    try {
      setLoading(true);
      const res = await api.createJob(jobType);
      setActiveJob(res);
    } catch (err: any) {
      alert(`Failed to launch job: ${err.message || err}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!activeJob) return;
    try {
      await api.cancelJob(activeJob.job_id);
      setActiveJob((prev) => (prev ? { ...prev, status: "cancelled", is_running: false } : null));
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
      </div>

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
