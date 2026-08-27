/**
 * JobStatusContext.test.tsx — the global "what's running right now" store.
 * Backed by GET /jobs (api.listJobs), polled every 3s, feeding TopStatusBar's
 * chip from any screen. Verifies: activeJobs is derived correctly (terminal
 * statuses excluded), isJobTypeActive/isCommandActive match on the right
 * field, and reload() re-fetches (the mechanism the poll and the modal's
 * Cancel button both rely on).
 */
import { renderHook, waitFor, act } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JobStatusProvider } from "./JobStatusContext";
import { useJobStatus } from "../hooks/useJobStatus";
import { api } from "../api/client";
import type { JobRecord } from "../api/types";

function wrapper({ children }: { children: React.ReactNode }) {
  return <JobStatusProvider>{children}</JobStatusProvider>;
}

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    job_id: "job-1",
    job_type: "command",
    status: "running",
    exit_code: null,
    is_running: true,
    cancellable: true,
    command_name: "backfill_news_history_from_audit.py",
    created_at: "2026-08-27T12:00:00+00:00",
    ...overrides,
  };
}

describe("JobStatusContext", () => {
  afterEach(() => vi.restoreAllMocks());

  it("outside a provider, degrades to the empty default rather than throwing", () => {
    const { result } = renderHook(() => useJobStatus());
    expect(result.current.jobs).toEqual([]);
    expect(result.current.activeJobs).toEqual([]);
    expect(result.current.isJobTypeActive("train_lgbm")).toBe(false);
  });

  it("fetches GET /jobs on mount and derives activeJobs, excluding terminal statuses", async () => {
    vi.spyOn(api, "listJobs").mockResolvedValueOnce({
      jobs: [
        job({ job_id: "running-1", status: "running", is_running: true }),
        job({ job_id: "done-1", status: "success", is_running: false, exit_code: 0 }),
        job({ job_id: "failed-1", status: "failed", is_running: false, exit_code: 1 }),
        job({ job_id: "cancelled-1", status: "cancelled", is_running: false, exit_code: -15 }),
      ],
    });

    const { result } = renderHook(() => useJobStatus(), { wrapper });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.jobs).toHaveLength(4);
    expect(result.current.activeJobs).toHaveLength(1);
    expect(result.current.activeJobs[0].job_id).toBe("running-1");
  });

  it("isJobTypeActive matches on job_type for non-command jobs", async () => {
    vi.spyOn(api, "listJobs").mockResolvedValueOnce({
      jobs: [job({ job_id: "t1", job_type: "train_lgbm", command_name: null })],
    });

    const { result } = renderHook(() => useJobStatus(), { wrapper });
    await waitFor(() => expect(result.current.isJobTypeActive("train_lgbm")).toBe(true));
    expect(result.current.isJobTypeActive("train_meta")).toBe(false);
  });

  it("isCommandActive matches on command_name for command-type jobs only", async () => {
    vi.spyOn(api, "listJobs").mockResolvedValueOnce({
      jobs: [job({ job_id: "c1", job_type: "command", command_name: "backfill_edgar_fundamentals.py" })],
    });

    const { result } = renderHook(() => useJobStatus(), { wrapper });
    await waitFor(() => expect(result.current.isCommandActive("backfill_edgar_fundamentals.py")).toBe(true));
    expect(result.current.isCommandActive("backfill_news_history.py")).toBe(false);
  });

  it("reload() re-fetches GET /jobs", async () => {
    const listJobsSpy = vi
      .spyOn(api, "listJobs")
      .mockResolvedValueOnce({ jobs: [] })
      .mockResolvedValueOnce({ jobs: [job()] });

    const { result } = renderHook(() => useJobStatus(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.activeJobs).toHaveLength(0);

    act(() => result.current.reload());

    await waitFor(() => expect(result.current.activeJobs).toHaveLength(1));
    expect(listJobsSpy).toHaveBeenCalledTimes(2);
  });

  it("polls GET /jobs on a fixed interval, independent of any auto-refresh gate", async () => {
    vi.useFakeTimers();
    const listJobsSpy = vi.spyOn(api, "listJobs").mockResolvedValue({ jobs: [] });

    renderHook(() => useJobStatus(), { wrapper });
    await act(async () => {});
    expect(listJobsSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(listJobsSpy).toHaveBeenCalledTimes(2);

    vi.useRealTimers();
  });
});
