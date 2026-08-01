import { useState } from "react";
import type { JobRecord } from "../api/types";
import { LogStream } from "./LogStream";
import { timeAgo } from "../format";
import { theme } from "../theme";
import { Button } from "./ui";

interface RecentRunsLogProps {
  jobs: JobRecord[];
  onRefresh?: () => void;
}

export function RecentRunsLog({ jobs, onRefresh }: RecentRunsLogProps) {
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  if (!jobs || jobs.length === 0) {
    return (
      <div
        style={{
          background: theme.surface,
          border: `1px solid ${theme.border}`,
          borderRadius: "var(--r-md)",
          padding: "var(--s-4)",
          textAlign: "center",
          color: theme.textMuted,
          fontSize: "var(--t-caption)",
        }}
        data-testid="recent-runs-empty"
      >
        No recent command execution history recorded.
      </div>
    );
  }

  return (
    <div
      style={{
        background: theme.surface,
        border: `1px solid ${theme.border}`,
        borderRadius: "var(--r-md)",
        padding: "var(--s-4)",
      }}
      data-testid="recent-runs-log"
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--s-3)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2)" }}>
          <span style={{ fontSize: "1.1rem" }}>📜</span>
          <span style={{ fontWeight: 700, fontSize: "var(--t-subhead)", color: theme.textPrimary }}>
            Recent Execution Runs ({jobs.length})
          </span>
        </div>
        {onRefresh && (
          <Button variant="neutral" onClick={onRefresh}>
            Refresh Log
          </Button>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-2)" }}>
        {jobs.map((job) => {
          const isSelected = selectedJobId === job.job_id;
          return (
            <div
              key={job.job_id}
              style={{
                border: `1px solid ${isSelected ? theme.borderStrong : theme.border}`,
                borderRadius: "var(--r-sm)",
                background: isSelected ? theme.surface2 : theme.surface,
                overflow: "hidden",
              }}
            >
              <div
                onClick={() => setSelectedJobId(isSelected ? null : job.job_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "var(--s-2-5) var(--s-3)",
                  cursor: "pointer",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "var(--s-2-5)" }}>
                  <StatusBadge status={job.status} />
                  <span style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)", fontWeight: 600, color: theme.textPrimary }}>
                    {job.command_name ?? job.job_type}
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "var(--s-3)", fontSize: "var(--t-caption)", color: theme.textMuted }}>
                  <span>{timeAgo(job.created_at)}</span>
                  <span>{isSelected ? "▲ Hide Log" : "▼ View Log"}</span>
                </div>
              </div>

              {isSelected && (
                <div style={{ borderTop: `1px solid ${theme.border}`, padding: "var(--s-3)", background: theme.surface3 }}>
                  <LogStream jobId={job.job_id} isStreaming={job.status === "running"} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  let color = theme.textMuted;
  let bg = "rgba(255, 255, 255, 0.1)";
  let label = status;

  if (status === "success" || status === "succeeded") {
    color = "#4ade80";
    bg = "rgba(74, 222, 128, 0.15)";
    label = "✓ Success (0)";
  } else if (status === "failed" || status === "error") {
    color = "#f87171";
    bg = "rgba(248, 113, 113, 0.15)";
    label = "✗ Failed (1)";
  } else if (status === "running") {
    color = "#38bdf8";
    bg = "rgba(56, 189, 248, 0.15)";
    label = "⚡ Running";
  }

  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 600,
        padding: "2px 8px",
        borderRadius: 4,
        color,
        background: bg,
      }}
    >
      {label}
    </span>
  );
}
