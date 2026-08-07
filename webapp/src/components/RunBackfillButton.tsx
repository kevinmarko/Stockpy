import React, { useState } from 'react';
import { useTasks } from '../hooks/TaskContext';
import { api } from '../api/client';

export const RunBackfillButton: React.FC = () => {
  const { startTask } = useTasks();

  const [loading, setLoading] = useState(false);

  const handleRunGlobalBackfill = async () => {
    if (loading) return;
    setLoading(true);
    try {
      const res = await api.runGlobalBackfill();
      startTask(res.job_id, 'Global Strategy Backfill & Meta-Labeling');
    } catch (error) {
      console.error("Failed to start backfill", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleRunGlobalBackfill}
      disabled={loading}
      className={`px-4 py-2 text-white font-medium rounded transition-colors flex items-center gap-2 ${loading ? 'bg-gray-400 cursor-not-allowed' : 'bg-emerald-600 hover:bg-emerald-500'}`}
    >
      <span>🚀</span>
      <span>{loading ? 'Starting...' : 'Run Full System Backfill'}</span>
    </button>
  );
};
