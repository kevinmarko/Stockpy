import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBackfillJob } from "./useBackfillJob";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { ForecastBackfillJob } from "../api/types";

function job(overrides: Partial<ForecastBackfillJob> = {}): ForecastBackfillJob {
  return {
    job_id: "job-1",
    state: "running",
    phase: "fetching_data",
    step: 1,
    total_steps: 7,
    error: null,
    summary: null,
    sample_rows: null,
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
      job({ state: "failed", phase: "backfilling", error: "OOM", seconds_remaining: 0 })
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
      .mockResolvedValueOnce(job({ state: "cancelled", error: "Job cancelled" }));
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
});
