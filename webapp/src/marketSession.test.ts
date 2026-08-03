/**
 * marketSession.test.ts — covers the pure `computeMarketSession` ET-time
 * classifier directly (weekday/weekend, RTH/pre-post/closed boundaries)
 * since driving that through the rendered TopStatusBar component would
 * require mocking the system timezone.
 */
import { describe, expect, it } from "vitest";
import { computeMarketSession } from "./marketSession";

describe("computeMarketSession", () => {
  // All instants below are given as UTC ISO strings so the test is
  // timezone-independent regardless of the machine running it; the function
  // itself converts to America/New_York internally.
  it("classifies a weekday during regular trading hours as RTH (Open)", () => {
    // 2026-07-16 is a Thursday. 14:00 UTC = 10:00 ET (summer, UTC-4).
    expect(computeMarketSession(new Date("2026-07-16T14:00:00Z"))).toBe("RTH (Open)");
  });

  it("classifies a weekday before the open as PRE/POST", () => {
    // 09:00 UTC = 05:00 ET.
    expect(computeMarketSession(new Date("2026-07-16T09:00:00Z"))).toBe("PRE/POST");
  });

  it("classifies a weekday late at night as CLOSED", () => {
    // 03:00 UTC = 23:00 ET (previous day).
    expect(computeMarketSession(new Date("2026-07-16T03:00:00Z"))).toBe("CLOSED");
  });

  it("classifies a Saturday as CLOSED even during what would be RTH hours on a weekday", () => {
    // 2026-07-18 is a Saturday. 14:00 UTC = 10:00 ET.
    expect(computeMarketSession(new Date("2026-07-18T14:00:00Z"))).toBe("CLOSED");
  });
});
