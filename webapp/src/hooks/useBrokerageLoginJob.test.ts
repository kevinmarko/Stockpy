import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useBrokerageLoginJob } from "./useBrokerageLoginJob";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { BrokerageLoginJob } from "../api/types";

function job(overrides: Partial<BrokerageLoginJob> = {}): BrokerageLoginJob {
  return {
    job_id: "job-1",
    mode: "connect",
    state: "running",
    phase: "starting",
    error_code: null,
    seconds_remaining: 180,
    connected: false,
    has_account_snapshot: false,
    ...overrides,
  };
}

describe("useBrokerageLoginJob", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("starts null, with no job and nothing pending", () => {
    const { result } = renderHook(() => useBrokerageLoginJob());
    expect(result.current.job).toBeNull();
    expect(result.current.starting).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("start('connect', creds) calls connectBrokerage and sets the initial running job", async () => {
    const connectSpy = vi
      .spyOn(api, "connectBrokerage")
      .mockResolvedValueOnce(job({ phase: "starting" }));
    const { result } = renderHook(() => useBrokerageLoginJob());

    await act(async () => {
      await result.current.start("connect", { username: "u@example.com", password: "pw" });
    });

    expect(connectSpy).toHaveBeenCalledWith({ username: "u@example.com", password: "pw" });
    expect(result.current.job).toEqual(job({ phase: "starting" }));
    expect(result.current.starting).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("start('refresh') calls refreshBrokerage with no credentials arg", async () => {
    const refreshSpy = vi
      .spyOn(api, "refreshBrokerage")
      .mockResolvedValueOnce(job({ mode: "refresh" }));
    const { result } = renderHook(() => useBrokerageLoginJob());

    await act(async () => {
      await result.current.start("refresh");
    });

    expect(refreshSpy).toHaveBeenCalledWith();
    expect(result.current.job?.mode).toBe("refresh");
  });

  it("polls getBrokerageLoginStatus every 2s while running, and stops the instant it succeeds", async () => {
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(
      job({ phase: "starting", seconds_remaining: 180 })
    );
    const statusSpy = vi
      .spyOn(api, "getBrokerageLoginStatus")
      .mockResolvedValueOnce(job({ phase: "awaiting_approval", seconds_remaining: 178 }))
      .mockResolvedValueOnce(job({ phase: "awaiting_approval", seconds_remaining: 176 }))
      .mockResolvedValueOnce(
        job({ state: "succeeded", phase: "done", seconds_remaining: 174, connected: true, has_account_snapshot: true })
      );

    const { result } = renderHook(() => useBrokerageLoginJob());
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
    });
    expect(statusSpy).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(1);
    expect(result.current.job?.phase).toBe("awaiting_approval");
    expect(result.current.job?.seconds_remaining).toBe(178);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(2);
    expect(result.current.job?.seconds_remaining).toBe(176);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(3);
    expect(result.current.job?.state).toBe("succeeded");
    expect(result.current.job?.connected).toBe(true);

    // Polling stopped -- no further calls even after another interval tick.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(3);
  });

  it("stops polling on a timeout and surfaces the honest terminal state", async () => {
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    vi.spyOn(api, "getBrokerageLoginStatus").mockResolvedValueOnce(
      job({ state: "timeout", phase: "awaiting_approval", error_code: "timeout", seconds_remaining: 0 })
    );

    const { result } = renderHook(() => useBrokerageLoginJob());
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.job?.state).toBe("timeout");
    expect(result.current.job?.error_code).toBe("timeout");

    const statusSpy = vi.mocked(api.getBrokerageLoginStatus);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).toHaveBeenCalledTimes(1); // no further poll once terminal
  });

  it("cancel() calls cancelBrokerageLogin and stops polling", async () => {
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    const cancelSpy = vi
      .spyOn(api, "cancelBrokerageLogin")
      .mockResolvedValueOnce({ ...job({ state: "cancelled", error_code: "cancelled" }), cancelled: true });
    const statusSpy = vi.spyOn(api, "getBrokerageLoginStatus");

    const { result } = renderHook(() => useBrokerageLoginJob());
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
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
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(
      job({ state: "succeeded", phase: "done", connected: true, has_account_snapshot: true })
    );
    const statusSpy = vi.spyOn(api, "getBrokerageLoginStatus");

    const { result } = renderHook(() => useBrokerageLoginJob());
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
    });
    expect(result.current.job?.state).toBe("succeeded");

    act(() => {
      result.current.reset();
    });

    expect(result.current.job).toBeNull();
    expect(result.current.error).toBeNull();

    // Nothing should still be polling after a reset.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusSpy).not.toHaveBeenCalled();
  });

  it("cancel() with no job started is a harmless no-op", async () => {
    const cancelSpy = vi.spyOn(api, "cancelBrokerageLogin");
    const { result } = renderHook(() => useBrokerageLoginJob());

    await act(async () => {
      await result.current.cancel();
    });

    expect(cancelSpy).not.toHaveBeenCalled();
    expect(result.current.job).toBeNull();
  });

  it("a connect() request failure (not a job-level failure) sets `error`, never a stuck spinner", async () => {
    vi.spyOn(api, "connectBrokerage").mockRejectedValueOnce(
      new ApiError("Could not reach the backend.", 502)
    );
    const { result } = renderHook(() => useBrokerageLoginJob());

    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
    });

    expect(result.current.error).toBe("Could not reach the backend.");
    expect(result.current.starting).toBe(false);
    expect(result.current.job).toBeNull();
  });

  it("a poll failure surfaces an honest error and stops polling rather than retrying forever", async () => {
    vi.spyOn(api, "connectBrokerage").mockResolvedValueOnce(job());
    const statusSpy = vi
      .spyOn(api, "getBrokerageLoginStatus")
      .mockRejectedValueOnce(new ApiError("Network error reaching the API.", 0));

    const { result } = renderHook(() => useBrokerageLoginJob());
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
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

  it("starting a new job supersedes a stale in-flight poll for the previous one", async () => {
    vi.spyOn(api, "connectBrokerage")
      .mockResolvedValueOnce(job({ job_id: "job-1" }))
      .mockResolvedValueOnce(job({ job_id: "job-2", phase: "authenticating" }));
    vi.spyOn(api, "getBrokerageLoginStatus").mockResolvedValueOnce(
      job({ job_id: "job-1", state: "succeeded", phase: "done" })
    );

    const { result } = renderHook(() => useBrokerageLoginJob());
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.job?.job_id).toBe("job-1");

    // Retry: a fresh start() before another poll on job-1 would fire.
    await act(async () => {
      await result.current.start("connect", { username: "u", password: "p" });
    });
    expect(result.current.job?.job_id).toBe("job-2");
  });
});
