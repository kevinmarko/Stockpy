import { api } from "../api/client";
import type { UniverseSymbol } from "../api/types";

// Split out of SymbolInput.tsx (pure module-level cache + helpers, no React)
// so that file only exports the `SymbolInput` component -- keeps Vite's
// React Fast Refresh working there instead of invalidating on every edit.

// Module-level cache: the universe is identical for every SymbolInput and rarely
// changes within a session, so fetch it at most once and share the result.
let universeCache: UniverseSymbol[] | null = null;
let universePromise: Promise<UniverseSymbol[]> | null = null;

/** The current cached universe, if any -- read-only snapshot for callers
 *  (e.g. SymbolInput's initial state) that want to avoid an extra await. */
export function getCachedUniverse(): UniverseSymbol[] | null {
  return universeCache;
}

/** Exported so other lookup UIs (e.g. CommandPaletteModal's ticker search)
 *  share this exact module cache instead of issuing their own GET /universe. */
export function loadUniverse(): Promise<UniverseSymbol[]> {
  if (universeCache) return Promise.resolve(universeCache);
  if (!universePromise) {
    universePromise = api
      .getUniverse()
      .then((r) => {
        universeCache = r.symbols ?? [];
        return universeCache;
      })
      .catch((err) => {
        // Non-fatal to the user: degrade to a plain text field (free-text still
        // works). Still log so a real outage is diagnosable rather than silently
        // indistinguishable from "nothing tracked yet". Reset the promise so a
        // later mount can retry.
        console.warn("SymbolInput: failed to load the tracked-symbol universe", err);
        universePromise = null;
        return [];
      });
  }
  return universePromise;
}

/** Exposed for tests to reset the shared cache between cases. */
export function __resetUniverseCache() {
  universeCache = null;
  universePromise = null;
}
