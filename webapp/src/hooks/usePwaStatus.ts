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
import { useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

export interface PwaStatus {
  /** False in browsers with no Service Worker API at all (e.g. some embedded webviews). */
  supported: boolean;
  /** True once the service worker has registered (fires `onRegisteredSW`). */
  registered: boolean;
  /** True once the SW has finished precaching — the app can now load offline. */
  offlineReady: boolean;
  /** True when a new SW version is installed and waiting to take over. */
  needRefresh: boolean;
  /** True if registration itself failed (fires `onRegisterError`). */
  registerError: boolean;
  /** Activates the waiting SW and reloads the page onto the new version. */
  update: () => void;
}

export function usePwaStatus(): PwaStatus {
  const supported =
    typeof navigator !== "undefined" && "serviceWorker" in navigator;
  const [registered, setRegistered] = useState(false);
  const [registerError, setRegisterError] = useState(false);

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

  return {
    supported,
    // offlineReady/needRefresh firing at all implies a live registration, even
    // if a stale `onRegisteredSW` render hasn't landed yet.
    registered: registered || offlineReady || needRefresh,
    offlineReady,
    needRefresh,
    registerError,
    update: () => {
      void updateServiceWorker(true);
    },
  };
}
