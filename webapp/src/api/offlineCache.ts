/**
 * offlineCache.ts — localStorage-backed response cache for the live API client.
 *
 * Every successful GET made through client.ts's `http()` is persisted here.
 * When a later request fails because the network is unreachable (fetch itself
 * throwing — client.ts's ApiError(status=0) case), `http()` looks up the last
 * cached response for that path and attaches it to the thrown error so the
 * `useApi` hook can serve it as real data instead of blanking the screen.
 *
 * Deliberately NOT consulted for a reachable server's own error response
 * (4xx/5xx) — that's a genuine failure, not an offline signal, and should
 * never be silently papered over with stale data.
 */

const PREFIX = "stockpy.cache.v1:";

export interface CacheEntry<T> {
  data: T;
  cachedAt: string; // ISO timestamp
}

export function readCacheEntry<T>(key: string): CacheEntry<T> | null {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CacheEntry<T>;
    if (!parsed || typeof parsed.cachedAt !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function writeCacheEntry<T>(key: string, data: T): void {
  try {
    const entry: CacheEntry<T> = { data, cachedAt: new Date().toISOString() };
    localStorage.setItem(PREFIX + key, JSON.stringify(entry));
  } catch {
    /* quota exceeded / storage disabled — caching is best-effort, never fatal */
  }
}
