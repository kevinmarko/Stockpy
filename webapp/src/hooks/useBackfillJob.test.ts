import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBackfillJob } from "./useBackfillJob";
import { api } from "../api/client";
import { ApiError, ForecastBackfillConflictError } from "../api/types";
import type { ForecastBackfillJob } from "../api/types";

function job(overrides: Partial<ForecastBackfillJob> = {}): ForecastBackfillJob {
  return {
    job_id: "job-1",
    state: "running",
    phase: "fetching_data",
    step: 1,
    total_steps: 7,
    error: null,
    error_type: null,
    summary: null,
    sample_rows: null,
    partial_summary: null,
    seconds_remaining: 14,
    ...overrides,
  };
}

describe("useBackfillJob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("starts null, with no job and nothing pending", () => {
    const { result } = renderHook(() => useBackfillJob());
    expect(result.current.job).toBeNull();
    expect(result.current.starting).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.notice).toBeNull();
  });

  it("start() calls runForecastBackfill and sets the initial running job", async () => {
    const runSpy = vi
      .spyOn(api, "runForecastBackfill")
      .mockResolvedValueOnce(job({ phase: "fetching_data" }));
    const { result } = renderHook(() => useBackfillJob());

    await act(async () => {
      await result.current.start({ theta_c: 0.5 });
    });

    expect(runSpy).toHaveBeenCalledWith({ theta_c: 0.5 });
    expect(result.current.job).toEqual(job({ phase: "fetching_data" }));
    expect(result.current.starting).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("polls getForecastBackfillJobStatus every 2s while running, and stops the instant it succeeds", async () => {
    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce(
      job({ phase: "fetching_data", seconds_remaining: 14 })
    );
    const statusSpy = vi
      .spyOn(api, "getForecastBackfillJobStatus")
      .mockResolvedValueOnce(job({ phase: "technical_features", seconds_remaining: 12 }))
      .mockResolvedValueOnce(job({ phase: "primary_signals", seconds_remaining: 10 }))
      .mockResolvedValueOnce(
        job({ state: "succeeded", phase: "exporting", seconds_remaining: 0, sample_rows: 1000 })
      );

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });
    expect(statusSpy).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(1);
    expect(result.current.job?.phase).toBe("technical_features");
    expect(result.current.job?.seconds_remaining).toBe(12);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(2);
    expect(result.current.job?.seconds_remaining).toBe(10);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(3);
    expect(result.current.job?.state).toBe("succeeded");
    expect(result.current.job?.sample_rows).toBe(1000);

    // Polling stopped
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(3);
  });

  it("stops polling on a failure and surfaces the honest terminal state", async () => {
    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce(job());
    vi.spyOn(api, "getForecastBackfillJobStatus").mockResolvedValueOnce(
      job({ state: "failed", phase: "backfilling", error: "OOM", error_type: "unexpected", seconds_remaining: 0 })
    );

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.job?.state).toBe("failed");
    expect(result.current.job?.error).toBe("OOM");
    expect(result.current.job?.error_type).toBe("unexpected");

    const statusSpy = vi.mocked(api.getForecastBackfillJobStatus);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(1);
  });

  it("cancel() calls cancelForecastBackfillJob and stops polling", async () => {
    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce(job());
    const cancelSpy = vi
      .spyOn(api, "cancelForecastBackfillJob")
      .mockResolvedValueOnce(job({ state: "cancelled", error: "Job cancelled", error_type: "cancelled" }));
    const statusSpy = vi.spyOn(api, "getForecastBackfillJobStatus");

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });

    await act(async () => {
      await result.current.cancel();
    });

    expect(cancelSpy).toHaveBeenCalledWith("job-1");
    expect(result.current.job?.state).toBe("cancelled");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).not.toHaveBeenCalled();
  });

  it("reset() clears job/error back to idle without touching the backend", async () => {
    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce(
      job({ state: "succeeded", phase: "exporting" })
    );
    const statusSpy = vi.spyOn(api, "getForecastBackfillJobStatus");

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.job?.state).toBe("succeeded");

    act(() => {
      result.current.reset();
    });

    expect(result.current.job).toBeNull();
    expect(result.current.error).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).not.toHaveBeenCalled();
  });

  it("a start() request failure (transport-level, not job-level) sets `error`, never a stuck spinner", async () => {
    vi.spyOn(api, "runForecastBackfill").mockRejectedValueOnce(
      new ApiError("Could not reach the backend.", 502)
    );
    const { result } = renderHook(() => useBackfillJob());

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.error).toBe("Could not reach the backend.");
    expect(result.current.starting).toBe(false);
    expect(result.current.job).toBeNull();
    expect(result.current.notice).toBeNull();
  });

  it("a poll failure surfaces an honest error and stops polling rather than retrying forever", async () => {
    vi.spyOn(api, "runForecastBackfill").mockResolvedValueOnce(job());
    const statusSpy = vi
      .spyOn(api, "getForecastBackfillJobStatus")
      .mockRejectedValueOnce(new ApiError("Network error reaching the API.", 0));

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.error).toBe("Network error reaching the API.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(1); // no retry storm
  });

  it("start() catches a 409 conflict and begins tracking the EXISTING job instead of erroring out", async () => {
    vi.spyOn(api, "runForecastBackfill").mockRejectedValueOnce(
      new ForecastBackfillConflictError("A forecast backfill run is already in progress.", "job-existing")
    );
    const statusSpy = vi
      .spyOn(api, "getForecastBackfillJobStatus")
      .mockResolvedValueOnce(job({ job_id: "job-existing", phase: "backtraining", step: 5 }));

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });

    // No dead-end error -- an honest "already running, now tracking it" notice.
    expect(result.current.error).toBeNull();
    expect(result.current.notice).toMatch(/already in progress/i);
    expect(result.current.job).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(statusSpy).toHaveBeenCalledWith("job-existing");
    expect(result.current.job?.job_id).toBe("job-existing");
    // The notice clears once the real job status has landed.
    expect(result.current.notice).toBeNull();
  });

  it("a stale poll response for a superseded job does not stomp the newer job's state", async () => {
    vi.spyOn(api, "runForecastBackfill")
      .mockResolvedValueOnce(job({ job_id: "job-a", phase: "fetching_data" }))
      .mockResolvedValueOnce(job({ job_id: "job-b", phase: "primary_signals" }));

    // jobA's status response is a deferred promise resolved LATE -- after
    // jobB has already superseded it as activeJobId -- reproducing the real
    // race: an interval tick fires a status fetch for jobA while it's still
    // the active job, then start() is called again before that fetch
    // resolves.
    let resolveJobAStatus!: (value: ForecastBackfillJob) => void;
    const jobAStatusPromise = new Promise<ForecastBackfillJob>((resolve) => {
      resolveJobAStatus = resolve;
    });
    const statusSpy = vi
      .spyOn(api, "getForecastBackfillJobStatus")
      .mockImplementationOnce(() => jobAStatusPromise);

    const { result } = renderHook(() => useBackfillJob());
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.job?.job_id).toBe("job-a");

    // Interval tick fires the (still-pending) jobA status fetch.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledWith("job-a");

    // A fresh start() supersedes activeJobId to job-b before jobA's fetch resolves.
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.job?.job_id).toBe("job-b");

    // NOW the stale jobA response arrives -- it must be discarded, not
    // applied over job-b's fresh state.
    await act(async () => {
      resolveJobAStatus(job({ job_id: "job-a", state: "succeeded", phase: "exporting" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(result.current.job?.job_id).toBe("job-b");
    expect(result.current.job?.state).toBe("running");
  });
});
