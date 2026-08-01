/**
 * RecentRunsLog.test.tsx — the execution-history list on the Commands
 * screen: honest empty state, status badges sourced from the real
 * `exit_code` (never a fabricated number), and expand/collapse of each
 * job's log panel.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RecentRunsLog } from "./RecentRunsLog";
import type { JobRecord } from "../api/types";

function job(overrides: Partial<JobRecord>): JobRecord {
  return {
    job_id: "job-1",
    job_type: "command",
    status: "running",
    cancellable: true,
    ...overrides,
  };
}

describe("RecentRunsLog", () => {
  it("renders an honest empty state, never a fabricated run", () => {
    render(<RecentRunsLog jobs={[]} />);
    expect(screen.getByTestId("recent-runs-empty")).toHaveTextContent(
      "No recent command execution history recorded."
    );
  });

  it("shows the job count and each job's command_name", () => {
    render(
      <RecentRunsLog
        jobs={[
          job({ job_id: "job-1", command_name: "validation.harness", status: "success", exit_code: 0 }),
          job({ job_id: "job-2", command_name: "scripts/preflight_check.py", status: "running" }),
        ]}
      />
    );
    expect(screen.getByText("Recent Execution Runs (2)")).toBeInTheDocument();
    expect(screen.getByText("validation.harness")).toBeInTheDocument();
    expect(screen.getByText("scripts/preflight_check.py")).toBeInTheDocument();
  });

  it("falls back to job_type when command_name is null (a non-command job type)", () => {
    render(<RecentRunsLog jobs={[job({ job_type: "preflight", command_name: null })]} />);
    expect(screen.getByText("preflight")).toBeInTheDocument();
  });

  it("a successful job shows its real exit code, never a hardcoded placeholder", () => {
    render(<RecentRunsLog jobs={[job({ status: "success", exit_code: 0 })]} />);
    expect(screen.getByText("✓ Success (exit 0)")).toBeInTheDocument();
  });

  it("a failed job shows its real non-zero exit code", () => {
    render(<RecentRunsLog jobs={[job({ status: "failed", exit_code: 137 })]} />);
    expect(screen.getByText("✗ Failed (exit 137)")).toBeInTheDocument();
  });

  it("omits the exit-code suffix entirely when exit_code is unknown", () => {
    render(<RecentRunsLog jobs={[job({ status: "success", exit_code: null })]} />);
    expect(screen.getByText("✓ Success")).toBeInTheDocument();
  });

  it("a running job shows no exit code", () => {
    render(<RecentRunsLog jobs={[job({ status: "running", exit_code: null })]} />);
    expect(screen.getByText("⚡ Running")).toBeInTheDocument();
  });

  it("clicking a job row expands its log panel, and clicking again collapses it", async () => {
    const user = userEvent.setup();
    render(<RecentRunsLog jobs={[job({ status: "running" })]} />);

    expect(screen.getByText("▼ View Log")).toBeInTheDocument();
    await user.click(screen.getByText("▼ View Log"));
    expect(screen.getByText("▲ Hide Log")).toBeInTheDocument();

    await user.click(screen.getByText("▲ Hide Log"));
    expect(screen.getByText("▼ View Log")).toBeInTheDocument();
  });

  it("Refresh Log is absent when onRefresh is not provided, present and wired when it is", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    const { rerender } = render(<RecentRunsLog jobs={[job({})]} />);
    expect(screen.queryByRole("button", { name: "Refresh Log" })).not.toBeInTheDocument();

    rerender(<RecentRunsLog jobs={[job({})]} onRefresh={onRefresh} />);
    await user.click(screen.getByRole("button", { name: "Refresh Log" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
