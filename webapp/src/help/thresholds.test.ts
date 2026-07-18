/**
 * thresholds.test.ts — the loadThresholds() cache: fetches at most once per
 * session (dedup across concurrent + sequential callers), and degrades to
 * `null` (never a fabricated fallback number) on fetch failure.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadThresholds, __resetThresholdsCache } from "./thresholds";
import { api } from "../api/client";

const LIVE = {
  pbo_max: 0.5,
  dsr_min: 0.95,
  net_sharpe_min: 0.5,
  max_drawdown_max: 0.3,
  stress_max_drawdown: 0.5,
  kelly_fraction: 0.5,
  kelly_cap: 0.2,
};

beforeEach(() => {
  __resetThresholdsCache();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loadThresholds", () => {
  it("resolves the live thresholds", async () => {
    vi.spyOn(api, "getThresholds").mockResolvedValue(LIVE);
    await expect(loadThresholds()).resolves.toEqual(LIVE);
  });

  it("fetches at most once — concurrent and later callers share the cache", async () => {
    const spy = vi.spyOn(api, "getThresholds").mockResolvedValue(LIVE);
    const [a, b] = await Promise.all([loadThresholds(), loadThresholds()]);
    const c = await loadThresholds();
    expect(a).toEqual(LIVE);
    expect(b).toEqual(LIVE);
    expect(c).toEqual(LIVE);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("degrades to null (not a fabricated fallback) when the fetch fails", async () => {
    vi.spyOn(api, "getThresholds").mockRejectedValue(new Error("offline"));
    await expect(loadThresholds()).resolves.toBeNull();
  });

  it("retries on the next call after a failure", async () => {
    const spy = vi
      .spyOn(api, "getThresholds")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(LIVE);

    await expect(loadThresholds()).resolves.toBeNull();
    await expect(loadThresholds()).resolves.toEqual(LIVE);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
