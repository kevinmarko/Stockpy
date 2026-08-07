import React, { useState } from 'react';
import { useTasks, TaskItem } from '../hooks/TaskContext';

export const TaskExecutionMonitor: React.FC = () => {
  const { tasks } = useTasks();
  const [isOpen, setIsOpen] = useState(false);

  const activeTasks = tasks.filter((t) => t.status === 'RUNNING' || t.status === 'PENDING');
  const finishedTasks = tasks.filter((t) => t.status === 'COMPLETED' || t.status === 'FAILED');

  return (
    <div className="relative font-mono text-xs">
      {/* Top Bar Indicator Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded border transition-colors ${
          activeTasks.length > 0
            ? 'bg-amber-950/40 border-amber-500/50 text-amber-300 animate-pulse'
            : 'bg-zinc-900 border-zinc-700 text-zinc-300 hover:bg-zinc-800'
        }`}
      >
        <span className="relative flex h-2 w-2">
          {activeTasks.length > 0 ? (
            <>
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
            </>
          ) : (
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          )}
        </span>
        <span>
          {activeTasks.length > 0
            ? `${activeTasks.length} Task(s) Running...`
            : 'Tasks Idle'}
        </span>
      </button>

      {/* Task Drawer */}
      {isOpen && (
        <div className="absolute right-0 mt-2 w-96 bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl z-50 p-4">
          <div className="flex justify-between items-center border-b border-zinc-800 pb-2 mb-3">
            <h3 className="font-bold text-zinc-200">System Execution Monitor</h3>
            <button
              onClick={() => setIsOpen(false)}
              className="text-zinc-500 hover:text-zinc-300"
            >
              ✕
            </button>
          </div>

          {/* Active Tasks Section */}
          <div className="space-y-3 mb-4">
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">
              Active Executions ({activeTasks.length})
            </div>
            {activeTasks.length === 0 ? (
              <div className="text-zinc-500 italic py-2 text-center">No active jobs</div>
            ) : (
              activeTasks.map((task) => (
                <TaskCard key={task.id} task={task} />
              ))
            )}
          </div>

          {/* Completed History */}
          <div className="space-y-2 max-h-48 overflow-y-auto">
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">
              Recent Activity
            </div>
            {finishedTasks.slice(0, 5).map((task) => (
              <div
                key={task.id}
                className="p-2 rounded bg-zinc-950 border border-zinc-800/80 flex justify-between items-center text-zinc-400"
              >
                <div>
                  <div className="font-medium text-zinc-300">{task.name}</div>
                  <div className="text-[10px] text-zinc-500">{task.message}</div>
                </div>
                <span
                  className={`px-1.5 py-0.5 text-[10px] rounded ${
                    task.status === 'COMPLETED'
                      ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                      : 'bg-rose-950 text-rose-400 border border-rose-800'
                  }`}
                >
                  {task.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const TaskCard: React.FC<{ task: TaskItem }> = ({ task }) => (
  <div className="p-3 bg-zinc-950 border border-zinc-800 rounded">
    <div className="flex justify-between items-center mb-1">
      <span className="font-medium text-zinc-200">{task.name}</span>
      <span className="text-[10px] text-amber-400 animate-pulse">Processing</span>
    </div>
    <div className="w-full bg-zinc-800 h-1.5 rounded-full overflow-hidden mb-1">
      <div
        className="bg-amber-500 h-full transition-all duration-300"
        style={{ width: `${task.progress || 0}%` }}
      />
    </div>
    <div className="flex justify-between text-[10px] text-zinc-500">
      <span>{task.message || 'Executing...'}</span>
      <span>{task.progress}%</span>
    </div>
  </div>
);
