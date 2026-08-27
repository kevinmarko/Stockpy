import { describe, expect, it } from "vitest";
import { buildApiRuntimeCaching } from "./vite.pwa-runtime-caching";

// The 4 real configured base URLs (webapp/src/config/env.ts::URL_DEFAULTS) --
// pinned here as literals (not imported) because vite.config.ts resolves
// them independently at build time, and this test exists specifically to
// catch the two configs drifting apart.
const API_BASE_URL = "http://localhost:8602";
const DATA_API_BASE_URL = "http://localhost:8603";
const METRICS_API_BASE_URL = "http://localhost:8604";
const CONTROL_API_BASE_URL = "http://localhost:8601";

const ALL_BASE_URLS = [API_BASE_URL, DATA_API_BASE_URL, METRICS_API_BASE_URL, CONTROL_API_BASE_URL];

// The REAL Server-Sent-Events job-status stream endpoint
// (webapp/src/api/client.ts::jobStreamUrl -> baseFor("/jobs") ->
// CONTROL_BASE_URL, consumed via a native EventSource in
// webapp/src/components/LogStream.tsx). A caching rule that intercepts
// this would break the live job-status stream -- this is the plan's
// explicit, named regression risk, so the test must exercise this exact
// shape and origin, not a generic stand-in path.
const REAL_STREAM_PATH = "/jobs/abc123-def456/stream";

describe("buildApiRuntimeCaching", () => {
  const rules = buildApiRuntimeCaching(ALL_BASE_URLS);
  const rule = rules[0];
  const pattern = rule.urlPattern as Function;

  it("builds a single NetworkFirst rule for all 4 configured base URLs", () => {
    expect(rules).toHaveLength(1);
    expect(rule.handler).toBe("NetworkFirst");
    expect(rule.options?.cacheName).toBe("api-cache");
    expect(rule.options?.networkTimeoutSeconds).toBe(4);
  });

  it.each([
    ["apiBaseUrl (:8602)", API_BASE_URL],
    ["dataApiBaseUrl (:8603)", DATA_API_BASE_URL],
    ["metricsApiBaseUrl (:8604)", METRICS_API_BASE_URL],
    ["controlApiBaseUrl (:8601)", CONTROL_API_BASE_URL],
  ])("matches a GET request to %s", (_label, base) => {
    expect(
      pattern({
        request: { method: "GET" },
        url: new URL(`${base}/api/v1/data`),
      }),
    ).toBe(true);
  });

  it("rejects a non-GET request to a configured origin", () => {
    expect(
      pattern({
        request: { method: "POST" },
        url: new URL(`${API_BASE_URL}/api/v1/data`),
      }),
    ).toBe(false);
  });

  it("rejects the REAL job-status SSE stream endpoint on its real origin (controlApiBaseUrl)", () => {
    // The regression this test exists to catch: a future edit that
    // "simplifies" the exclusion predicate must fail HERE, on the actual
    // endpoint shape/origin the app uses -- not on a generic stand-in path
    // that happens to also end in "/stream" but was never exercised on the
    // control-API origin the real SSE traffic actually flows through.
    expect(
      pattern({
        request: { method: "GET" },
        url: new URL(`${CONTROL_API_BASE_URL}${REAL_STREAM_PATH}`),
      }),
    ).toBe(false);
  });

  it("rejects a generic /stream-suffixed path on any of the other 3 origins too", () => {
    for (const base of [API_BASE_URL, DATA_API_BASE_URL, METRICS_API_BASE_URL]) {
      expect(
        pattern({
          request: { method: "GET" },
          url: new URL(`${base}/api/v1/stream`),
        }),
      ).toBe(false);
    }
  });

  it("rejects an unconfigured origin", () => {
    expect(
      pattern({
        request: { method: "GET" },
        url: new URL("http://localhost:9999/api/v1/data"),
      }),
    ).toBe(false);
  });
});
