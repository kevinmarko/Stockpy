/**
 * usePwaStatus.test.ts — covers the durable offline-cache probe.
 *
 * `useRegisterSW`'s `offlineReady` is a one-shot EVENT (workbox-window's SW
 * `installed` event), so it is false on every visit AFTER the one in which the
 * worker first installed — even though the app is fully precached. The hook
 * therefore also probes Cache Storage directly; these tests pin that behavior,
 * including the deliberate dev-server opt-out (the dev SW precaches only a
 * fallback shell, so claiming offline readiness there would be a lie).
 */
import { renderHook, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePwaStatus } from "./usePwaStatus";

let offlineReadyEvent = false;

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: (opts?: { onRegisteredSW?: () => void }) => {
    useEffect(() => {
      opts?.onRegisteredSW?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return {
      needRefresh: [false, vi.fn()],
      offlineReady: [offlineReadyEvent, vi.fn()],
      updateServiceWorker: vi.fn(),
    };
  },
}));

/** Minimal Cache Storage stub: cache name → entry count. */
function stubCaches(contents: Record<string, number> | Error) {
  const value =
    contents instanceof Error
      ? {
          keys: () => Promise.reject(contents),
          open: () => Promise.reject(contents),
        }
      : {
          keys: () => Promise.resolve(Object.keys(contents)),
          open: (name: string) =>
            Promise.resolve({
              keys: () =>
                Promise.resolve(Array.from({ length: contents[name] ?? 0 }, (_, i) => `/asset-${i}`)),
            }),
        };
  vi.stubGlobal("caches", value);
}

function stubServiceWorker() {
  Object.defineProperty(navigator, "serviceWorker", {
    value: {},
    configurable: true,
  });
}

afterEach(() => {
  offlineReadyEvent = false;
  delete (navigator as { serviceWorker?: unknown }).serviceWorker;
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("usePwaStatus offline-cache probe", () => {
  it("reports offline-ready from a populated precache even when the install event never fires", async () => {
    vi.stubEnv("PROD", true);
    stubServiceWorker();
    stubCaches({ "workbox-precache-v2-http://localhost/": 42 });

    const { result } = renderHook(() => usePwaStatus());

    expect(result.current.offlineReady).toBe(false); // probe is async
    await waitFor(() => expect(result.current.offlineReady).toBe(true));
  });

  it("stays not-cached when the precache exists but is empty", async () => {
    vi.stubEnv("PROD", true);
    stubServiceWorker();
    stubCaches({ "workbox-precache-v2-http://localhost/": 0 });

    const { result } = renderHook(() => usePwaStatus());

    await waitFor(() => expect(result.current.registered).toBe(true));
    expect(result.current.offlineReady).toBe(false);
  });

  it("ignores non-precache caches (runtime caches are not offline readiness)", async () => {
    vi.stubEnv("PROD", true);
    stubServiceWorker();
    stubCaches({ "pilots-api-runtime": 12 });

    const { result } = renderHook(() => usePwaStatus());

    await waitFor(() => expect(result.current.registered).toBe(true));
    expect(result.current.offlineReady).toBe(false);
  });

  it("degrades to not-cached (never throws) when Cache Storage is blocked", async () => {
    vi.stubEnv("PROD", true);
    stubServiceWorker();
    stubCaches(new Error("SecurityError: storage blocked"));

    const { result } = renderHook(() => usePwaStatus());

    await waitFor(() => expect(result.current.registered).toBe(true));
    expect(result.current.offlineReady).toBe(false);
  });

  it("does not probe (and flags isDev) under the dev server, where only a fallback shell is precached", async () => {
    vi.stubEnv("PROD", false);
    stubServiceWorker();
    stubCaches({ "workbox-precache-v2-http://localhost/": 42 });

    const { result } = renderHook(() => usePwaStatus());

    await waitFor(() => expect(result.current.registered).toBe(true));
    expect(result.current.isDev).toBe(true);
    expect(result.current.offlineReady).toBe(false);
  });

  it("still honors the install event on the first-ever load", () => {
    vi.stubEnv("PROD", true);
    offlineReadyEvent = true;
    stubServiceWorker();
    stubCaches({});

    const { result } = renderHook(() => usePwaStatus());

    expect(result.current.offlineReady).toBe(true);
  });
});
