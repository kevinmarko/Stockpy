import { describe, expect, it } from "vitest";
import { buildApiRuntimeCaching } from "./vite.pwa-runtime-caching";

describe("buildApiRuntimeCaching", () => {
  it("builds a caching rule for given base URLs", () => {
    const baseUrls = [
      "http://localhost:8602",
      "https://example.com/api/",
    ];
    
    const rules = buildApiRuntimeCaching(baseUrls);
    expect(rules).toHaveLength(1);
    
    const rule = rules[0];
    expect(rule.handler).toBe("NetworkFirst");
    expect(rule.options?.cacheName).toBe("api-cache");
    expect(rule.options?.networkTimeoutSeconds).toBe(4);
    
    const pattern = rule.urlPattern as Function;
    
    // Matches GET request to base URL
    expect(pattern({
      request: { method: "GET" },
      url: new URL("http://localhost:8602/api/v1/data")
    })).toBe(true);

    // Matches GET request to other base URL
    expect(pattern({
      request: { method: "GET" },
      url: new URL("https://example.com/api/v1/data")
    })).toBe(true);
    
    // Rejects non-GET
    expect(pattern({
      request: { method: "POST" },
      url: new URL("http://localhost:8602/api/v1/data")
    })).toBe(false);

    // Rejects stream endpoint
    expect(pattern({
      request: { method: "GET" },
      url: new URL("http://localhost:8602/api/v1/stream")
    })).toBe(false);

    // Rejects unknown origin
    expect(pattern({
      request: { method: "GET" },
      url: new URL("http://localhost:8605/api/v1/data")
    })).toBe(false);
  });
});
