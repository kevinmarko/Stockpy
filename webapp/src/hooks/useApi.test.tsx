/**
 * useApi.test.tsx — the shared async-loader hook. Covers the offline-cache
 * fallback contract (Web App Resilience gap): when client.ts's `http()`
 * attaches `cachedData`/`cachedAt` to a network-failure ApiError, `useApi`
 * must serve that as real `data` (not an error screen) and flag `stale`,
 * while a plain ApiError (no cachedData — a reachable server's own error,
 * or a mocked rejection with no cache behind it) keeps the original
 * error/status behavior untouched.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useApi } from "./useApi";
import { ApiError } from "../api/client";

describe("useApi", () => {
  it("a successful resolve sets data and clears stale/cachedAt", async () => {
    const { result } = renderHook(() => useApi(() => Promise.resolve(["a"]), []));

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toEqual(["a"]);
    expect(result.current.error).toBeNull();
    expect(result.current.status).toBeNull();
    expect(result.current.stale).toBe(false);
    expect(result.current.cachedAt).toBeNull();
  });

  it("an ApiError carrying cachedData resolves as stale data, not an error screen", async () => {
    const err = new ApiError("Network error reaching Pilots API", 0);
    err.cachedData = ["cached-pilot"];
    err.cachedAt = "2026-07-01T00:00:00.000Z";

    const { result } = renderHook(() => useApi(() => Promise.reject(err), []));

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toEqual(["cached-pilot"]);
    expect(result.current.stale).toBe(true);
    expect(result.current.cachedAt).toBe("2026-07-01T00:00:00.000Z");
    expect(result.current.error).toBeNull();
    expect(result.current.status).toBeNull();
  });

  it("a plain ApiError with no cachedData still surfaces as a hard error (unchanged behavior)", async () => {
    const err = new ApiError("backend unreachable", 500);
    const { result } = renderHook(() => useApi(() => Promise.reject(err), []));

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data).toBeNull();
    expect(result.current.stale).toBe(false);
    expect(result.current.cachedAt).toBeNull();
    expect(result.current.error).toBe("backend unreachable");
    expect(result.current.status).toBe(500);
  });

  it("a non-ApiError rejection falls back to a generic error message", async () => {
    const { result } = renderHook(() =>
      useApi(() => Promise.reject(new Error("boom")), [])
    );

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("boom");
    expect(result.current.status).toBeNull();
    expect(result.current.stale).toBe(false);
  });

  it("reload() re-invokes fn and can transition from stale-cached back to fresh data", async () => {
    let call = 0;
    const fn = () => {
      call++;
      if (call === 1) {
        const err = new ApiError("offline", 0);
        err.cachedData = ["stale"];
        err.cachedAt = "2026-07-01T00:00:00.000Z";
        return Promise.reject(err);
      }
      return Promise.resolve(["fresh"]);
    };

    const { result } = renderHook(() => useApi(fn, []));
    await waitFor(() => expect(result.current.stale).toBe(true));
    expect(result.current.data).toEqual(["stale"]);

    act(() => result.current.reload());

    await waitFor(() => expect(result.current.stale).toBe(false));
    expect(result.current.data).toEqual(["fresh"]);
    expect(result.current.cachedAt).toBeNull();
  });
});
