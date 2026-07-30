/**
 * apiToken.test.ts — runtime API bearer token resolution.
 *
 * The behavior under test exists specifically because VITE_API_TOKEN is
 * baked into the built JS bundle at build time -- readable by anyone who can
 * load the page, which is fine on loopback (only this machine can load it)
 * but a real leak the moment the bundle is served over LAN/Tailscale.
 * getEffectiveToken()'s job is to stop falling back to that build-time value
 * the moment the origin isn't loopback.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getEffectiveToken,
  getStoredToken,
  isLoopbackOrigin,
  needsTokenEntry,
  setStoredToken,
} from "./apiToken";

const SESSION_KEY = "stockpy.api_token";

function setHostname(hostname: string) {
  Object.defineProperty(window, "location", {
    value: { ...window.location, hostname },
    writable: true,
  });
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  setHostname("localhost");
});

describe("isLoopbackOrigin", () => {
  it("treats localhost, 127.0.0.1, and ::1 as loopback", () => {
    for (const host of ["localhost", "127.0.0.1", "::1"]) {
      setHostname(host);
      expect(isLoopbackOrigin()).toBe(true);
    }
  });

  it("treats a LAN/Tailscale hostname as non-loopback", () => {
    setHostname("192.168.1.42");
    expect(isLoopbackOrigin()).toBe(false);
  });
});

describe("getStoredToken / setStoredToken", () => {
  it("round-trips through sessionStorage", () => {
    expect(getStoredToken()).toBe("");
    setStoredToken("abc123");
    expect(getStoredToken()).toBe("abc123");
    expect(sessionStorage.getItem(SESSION_KEY)).toBe("abc123");
  });

  it("clearing with an empty string removes the stored token", () => {
    setStoredToken("abc123");
    setStoredToken("");
    expect(getStoredToken()).toBe("");
    expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("degrades to empty string when sessionStorage throws (private browsing)", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    expect(getStoredToken()).toBe("");
    spy.mockRestore();
  });
});

describe("getEffectiveToken", () => {
  it("prefers a stored token over the build-time fallback on loopback", () => {
    setHostname("localhost");
    setStoredToken("stored-token");
    expect(getEffectiveToken()).toBe("stored-token");
  });

  it("falls back to VITE_API_TOKEN only on loopback with nothing stored", () => {
    setHostname("127.0.0.1");
    vi.stubEnv("VITE_API_TOKEN", "build-time-token");
    expect(getEffectiveToken()).toBe("build-time-token");
  });

  it("never falls back to the build-time token on a non-loopback origin", () => {
    setHostname("192.168.1.42");
    vi.stubEnv("VITE_API_TOKEN", "build-time-token");
    expect(getEffectiveToken()).toBe("");
  });

  it("uses a stored token on a non-loopback origin too", () => {
    setHostname("192.168.1.42");
    setStoredToken("entered-by-operator");
    expect(getEffectiveToken()).toBe("entered-by-operator");
  });
});

describe("needsTokenEntry", () => {
  it("is false on loopback regardless of whether a token is configured", () => {
    setHostname("localhost");
    expect(needsTokenEntry()).toBe(false);
  });

  it("is true on a non-loopback origin with no token available", () => {
    setHostname("192.168.1.42");
    expect(needsTokenEntry()).toBe(true);
  });

  it("is false on a non-loopback origin once a token is stored", () => {
    setHostname("192.168.1.42");
    setStoredToken("entered-by-operator");
    expect(needsTokenEntry()).toBe(false);
  });
});
