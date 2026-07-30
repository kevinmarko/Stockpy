/**
 * Runtime API bearer token resolution.
 *
 * `VITE_API_TOKEN` is baked into the built JS bundle at build time -- fine
 * when the bundle only ever runs on the same machine as the backend
 * (`npm run dev` on localhost), but a real leak vector the moment that same
 * bundle is served to a browser on another device (LAN/Tailscale access):
 * anyone who can load the page can read the secret out of it.
 *
 * On a non-loopback origin the token instead comes from `sessionStorage`
 * (entered once via <TokenGate>, never bundled, cleared when the tab
 * closes). `VITE_API_TOKEN` is used as a fallback ONLY on loopback origins,
 * so the zero-config `npm run dev` experience is unaffected.
 */

const SESSION_KEY = "stockpy.api_token";

const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1"]);

export function isLoopbackOrigin(): boolean {
  return LOOPBACK_HOSTNAMES.has(window.location.hostname);
}

export function getStoredToken(): string {
  try {
    return sessionStorage.getItem(SESSION_KEY) ?? "";
  } catch {
    return ""; // sessionStorage unavailable (private browsing, etc.)
  }
}

export function setStoredToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(SESSION_KEY, token);
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
}

/** The token client.ts should actually send with every request. */
export function getEffectiveToken(): string {
  const stored = getStoredToken();
  if (stored) return stored;
  if (isLoopbackOrigin()) return import.meta.env.VITE_API_TOKEN ?? "";
  return "";
}

/** True when a non-loopback origin has no usable token yet -- <TokenGate> should show. */
export function needsTokenEntry(): boolean {
  return !isLoopbackOrigin() && !getEffectiveToken();
}
