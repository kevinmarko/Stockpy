/**
 * format.test.ts — unit tests for the shared formatting helpers in format.ts.
 *
 * These are pure functions with no DOM/React dependency and no prior test
 * coverage (flagged in the 2026-07-14 test-coverage re-audit's Phase 5
 * roadmap: "webapp/ has vitest configured but only one test file"). Every
 * screen in this app reads through these for currency/percent/date display,
 * so a formatting regression here would silently corrupt what every user
 * sees.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { fmtUsd, fmtPct, fmtNum, fmtSignedUsd, fmtDate, timeAgo } from "./format";

describe("fmtUsd", () => {
  it("formats a positive value with two decimals", () => {
    expect(fmtUsd(1234.5)).toBe("$1,234.50");
  });

  it("formats zero", () => {
    expect(fmtUsd(0)).toBe("$0.00");
  });

  it("formats a negative value with the standard Intl minus-sign convention", () => {
    expect(fmtUsd(-42)).toBe("-$42.00");
  });

  it("returns the em-dash sentinel for null/undefined/NaN", () => {
    expect(fmtUsd(null)).toBe("—");
    expect(fmtUsd(undefined)).toBe("—");
    expect(fmtUsd(NaN)).toBe("—");
  });

  it("compact mode abbreviates large values", () => {
    expect(fmtUsd(1_500_000, { compact: true })).toBe("$1.5M");
  });

  it("compact mode does not abbreviate small values", () => {
    expect(fmtUsd(500, { compact: true })).toBe("$500.00");
  });
});

describe("fmtPct", () => {
  it("formats a plain percent value with one decimal by default", () => {
    expect(fmtPct(12.345)).toBe("12.3%");
  });

  it("respects a custom digits count", () => {
    expect(fmtPct(12.345, 2)).toBe("12.35%");
  });

  it("converts a fraction to a percent when fromFraction is set", () => {
    expect(fmtPct(0.125, 1, { fromFraction: true })).toBe("12.5%");
  });

  it("prefixes a '+' for positive values when signed is set", () => {
    expect(fmtPct(5, 0, { signed: true })).toBe("+5%");
  });

  it("does not prefix '+' for zero or negative values even when signed", () => {
    expect(fmtPct(0, 0, { signed: true })).toBe("0%");
    expect(fmtPct(-5, 0, { signed: true })).toBe("-5%");
  });

  it("returns the em-dash sentinel for null/undefined/NaN", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtPct(NaN)).toBe("—");
  });
});

describe("fmtNum", () => {
  it("formats with two decimals by default", () => {
    expect(fmtNum(3.14159)).toBe("3.14");
  });

  it("respects a custom digits count", () => {
    expect(fmtNum(3.14159, 0)).toBe("3");
  });

  it("returns the em-dash sentinel for null/undefined/NaN", () => {
    expect(fmtNum(null)).toBe("—");
    expect(fmtNum(undefined)).toBe("—");
    expect(fmtNum(NaN)).toBe("—");
  });
});

describe("fmtSignedUsd", () => {
  it("prefixes '+' for a positive value", () => {
    expect(fmtSignedUsd(100)).toBe("+$100.00");
  });

  it("prefixes '-' for a negative value (not a double sign)", () => {
    expect(fmtSignedUsd(-100)).toBe("-$100.00");
  });

  it("prefixes '+' for exactly zero", () => {
    expect(fmtSignedUsd(0)).toBe("+$0.00");
  });

  it("returns the em-dash sentinel for null/undefined/NaN", () => {
    expect(fmtSignedUsd(null)).toBe("—");
    expect(fmtSignedUsd(undefined)).toBe("—");
    expect(fmtSignedUsd(NaN)).toBe("—");
  });
});

describe("fmtDate", () => {
  it("formats a valid ISO date as 'Mon D'", () => {
    expect(fmtDate("2026-03-15T00:00:00Z")).toBe("Mar 15");
  });

  it("returns the em-dash sentinel for null/undefined/empty string", () => {
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate(undefined)).toBe("—");
    expect(fmtDate("")).toBe("—");
  });

  it("returns the em-dash sentinel for an unparseable string, never throws", () => {
    expect(fmtDate("not-a-date")).toBe("—");
  });
});

describe("timeAgo", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 'unknown' for null/undefined", () => {
    expect(timeAgo(null)).toBe("unknown");
    expect(timeAgo(undefined)).toBe("unknown");
  });

  it("returns 'unknown' for an unparseable string, never throws", () => {
    expect(timeAgo("not-a-date")).toBe("unknown");
  });

  it("returns 'just now' for a timestamp under 30 seconds old", () => {
    // mins = Math.round(delta / 60000); a 30s delta rounds UP to 1 ("1m
    // ago"), so this needs a delta comfortably under the 30s rounding
    // midpoint to land on "just now" (mins === 0).
    const now = new Date("2026-01-01T12:00:10Z");
    vi.useFakeTimers();
    vi.setSystemTime(now);
    expect(timeAgo("2026-01-01T12:00:00Z")).toBe("just now");
  });

  it("returns minutes-ago for a timestamp under an hour old", () => {
    const now = new Date("2026-01-01T12:30:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(now);
    expect(timeAgo("2026-01-01T12:00:00Z")).toBe("30m ago");
  });

  it("returns hours-ago for a timestamp under a day old", () => {
    const now = new Date("2026-01-02T06:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(now);
    expect(timeAgo("2026-01-02T00:00:00Z")).toBe("6h ago");
  });

  it("returns days-ago for a timestamp over a day old", () => {
    const now = new Date("2026-01-10T00:00:00Z");
    vi.useFakeTimers();
    vi.setSystemTime(now);
    expect(timeAgo("2026-01-05T00:00:00Z")).toBe("5d ago");
  });
});
