/**
 * usePwaStatus.ts — operator-visible telemetry for the installed service
 * worker (PWA Resilience gap: "no operator UI feedback indicating whether
 * [service workers] are active, caching successfully, or running on the
 * latest updated version").
 *
 * Wraps vite-plugin-pwa's `virtual:pwa-register/react` hook, which in a
 * production build performs the real Workbox registration; under `vite dev`/
 * `vitest` (no `command === "build"`) the plugin swaps in an inert no-op
 * stub, so this hook is always safe to mount regardless of environment.
 */
import { useEffect, useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

export interface PwaStatus {
  /** False in browsers with no Service Worker API at all (e.g. some embedded webviews). */
  supported: boolean;
  /** True once the service worker has registered (fires `onRegisteredSW`). */
  registered: boolean;
  /**
   * True when the app is actually precached and can load offline — either
   * because precaching just finished this page load, or because a populated
   * Workbox precache is already sitting in Cache Storage from a previous one.
   */
  offlineReady: boolean;
  /** True when a new SW version is installed and waiting to take over. */
  needRefresh: boolean;
  /** True if registration itself failed (fires `onRegisterError`). */
  registerError: boolean;
  /**
   * True when running against the Vite dev server. The dev service worker
   * precaches only a fallback shell + the Workbox runtime — never the app
   * modules, which Vite serves per-request — so offline readiness is not a
   * meaningful (or honestly reportable) state in dev.
   */
  isDev: boolean;
  /** Activates the waiting SW and reloads the page onto the new version. */
  update: () => void;
}

/**
 * Does Cache Storage already hold a populated Workbox precache?
 *
 * This exists because `offlineReady` from `useRegisterSW` is a one-shot EVENT,
 * not a state query: workbox-window derives it from the SW `installed` event,
 * which fires only on the single page load during which the worker first
 * installs. An already-installed, already-precaching SW fires nothing on
 * subsequent visits, so the event-only signal reads false forever after —
 * reporting "Not cached yet" for an app that is in fact fully cached. Reading
 * the cache is the durable check.
 *
 * Returns false (never throws) when Cache Storage is unavailable or blocked —
 * private-browsing modes and insecure origins both reject here.
 */
async function precacheIsPopulated(): Promise<boolean> {
  if (typeof caches === "undefined") return false;
  try {
    const names = await caches.keys();
    for (const name of names) {
      // Workbox names its precache `workbox-precache-v2-<scope>`.
      if (!name.includes("precache")) continue;
      const entries = await (await caches.open(name)).keys();
      if (entries.length > 0) return true;
    }
  } catch {
    return false;
  }
  return false;
}

export function usePwaStatus(): PwaStatus {
  const supported =
    typeof navigator !== "undefined" && "serviceWorker" in navigator;
  const isDev = !import.meta.env.PROD;
  const [registered, setRegistered] = useState(false);
  const [registerError, setRegisterError] = useState(false);
  const [precached, setPrecached] = useState(false);

  const {
    needRefresh: [needRefresh],
    offlineReady: [offlineReady],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW() {
      setRegistered(true);
    },
    onRegisterError() {
      setRegisterError(true);
    },
  });

  // Re-probe whenever the registration state changes: on a first-ever visit the
  // precache is still filling when this first runs (the `onOfflineReady` event
  // covers that load), and on every later visit the probe is the only signal.
  useEffect(() => {
    if (!supported || isDev || offlineReady) return;
    let cancelled = false;
    void precacheIsPopulated().then((populated) => {
      if (!cancelled && populated) setPrecached(true);
    });
    return () => {
      cancelled = true;
    };
  }, [supported, isDev, offlineReady, registered, needRefresh]);

  return {
    supported,
    // offlineReady/needRefresh firing at all implies a live registration, even
    // if a stale `onRegisteredSW` render hasn't landed yet.
    registered: registered || offlineReady || needRefresh,
    offlineReady: offlineReady || precached,
    needRefresh,
    registerError,
    isDev,
    update: () => {
      void updateServiceWorker(true);
    },
  };
}
