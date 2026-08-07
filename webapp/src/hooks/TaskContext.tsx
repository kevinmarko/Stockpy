import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { api } from '../api/client';

export type TaskStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface TaskItem {
  id: string;
  name: string;
  status: TaskStatus;
  startTime: Date;
  endTime?: Date;
  progress?: number; // 0 - 100
  message?: string;
}

interface TaskContextType {
  tasks: TaskItem[];
  startTask: (id: string, name: string) => void;
  updateTaskProgress: (id: string, progress: number, message?: string) => void;
  completeTask: (id: string, message?: string) => void;
  failTask: (id: string, message?: string) => void;
}

const TaskContext = createContext<TaskContextType | undefined>(undefined);

export const TaskProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [tasks, setTasks] = useState<TaskItem[]>([]);

  useEffect(() => {
    const activeTaskIds = tasks
      .filter((t) => t.status === 'PENDING' || t.status === 'RUNNING')
      .map((t) => t.id);

    if (activeTaskIds.length === 0) return;

    let isSubscribed = true;
    const poll = async () => {
      for (const id of activeTaskIds) {
        try {
          const status = await api.getGlobalBackfillStatus(id);
          if (!isSubscribed) return;
          
          setTasks((prev) =>
            prev.map((t) =>
              t.id === id
                ? {
                    ...t,
                    status: status.status,
                    progress: status.progress,
                    message: status.message,
                    endTime: status.end_time ? new Date(status.end_time) : undefined,
                  }
                : t
            )
          );
        } catch (err) {
          console.error(`Failed to poll task ${id}:`, err);
        }
      }
    };

    const intervalId = setInterval(poll, 2500);
    return () => {
      isSubscribed = false;
      clearInterval(intervalId);
    };
  }, [tasks.map((t) => t.status).join(",")]);

  const startTask = (id: string, name: string) => {
    setTasks((prev) => [
      { id, name, status: 'RUNNING', startTime: new Date(), progress: 0 },
      ...prev.filter((t) => t.id !== id),
    ]);
  };

  const updateTaskProgress = (id: string, progress: number, message?: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, progress, message } : t))
    );
  };

  const completeTask = (id: string, message?: string) => {
    setTasks((prev) =>
      prev.map((t) =>
        t.id === id
          ? { ...t, status: 'COMPLETED', endTime: new Date(), progress: 100, message }
          : t
      )
    );
  };

  const failTask = (id: string, message?: string) => {
    setTasks((prev) =>
      prev.map((t) =>
        t.id === id
          ? { ...t, status: 'FAILED', endTime: new Date(), message }
          : t
      )
    );
  };

  return (
    <TaskContext.Provider
      value={{ tasks, startTask, updateTaskProgress, completeTask, failTask }}
    >
      {children}
    </TaskContext.Provider>
  );
};

export const useTasks = () => {
  const context = useContext(TaskContext);
  if (!context) {
    throw new Error('useTasks must be used within a TaskProvider');
  }
  return context;
};
