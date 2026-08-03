/**
 * env.ts — fail-fast validation of every `VITE_*` build-time setting.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * Two silent-misconfiguration classes used to be possible, both of which
 * degrade into *plausible-looking wrong behaviour* rather than an error:
 *
 *  1. `VITE_USE_MOCK` was read as `(raw ?? "true").toLowerCase() !== "false"`.
 *     So `0`, `off`, a typo'd key, or `flase` all left the app silently in
 *     MOCK mode — rendering fabricated data that looks exactly like a real
 *     portfolio. An operator flipping the flag to go live could believe they
 *     were watching real positions when they were not.
 *
 *  2. The four base URLs were read as `import.meta.env.X ?? "<default>"`.
 *     `??` is nullish-only, and Vite's `loadEnv()` applies no truthiness
 *     filter, so a present-but-empty `VITE_API_BASE_URL=` arrives as `""`,
 *     survives the `??`, and turns every `fetch(`${base}${path}`)` into a
 *     RELATIVE same-origin request against whatever happens to be serving
 *     the page. Four keys wide.
 *
 * DESIGN
 * ------
 * `parseEnv()` is PURE: it never touches `import.meta.env` or `window`. That
 * keeps it table-testable without `vi.stubEnv`/`vi.resetModules` gymnastics,
 * callable from Node for a build-time check, and makes the loopback token
 * rule testable by passing `hostname` explicitly instead of hiding a
 * `window.location` read inside it.
 *
 * This module MUST stay a leaf — it imports nothing from the rest of the
 * project, so `auth/apiToken.ts` can import `LOOPBACK_HOSTNAMES` from here
 * without creating a cycle.
 *
 * It also MUST NOT throw. ESM evaluates the whole import graph before
 * `main.tsx`'s body runs, and a throw during module evaluation yields a blank
 * white page that no React error boundary can ever catch (boundaries catch
 * render/lifecycle errors, never module-eval errors). So parsing always
 * finishes and merely *reports* issues, which `main.tsx` renders via
 * <EnvError/>.
 */

/**
 * Hostnames treated as "same machine as the backend".
 *
 * Single source of truth: `auth/apiToken.ts` imports this rather than keeping
 * its own copy, and the token rules below reason about the same three names.
 */
export const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1"]);

export interface EnvIssue {
  key: string;
  severity: "error" | "warning";
  message: string;
}

export interface AppConfig {
  apiBaseUrl: string;
  dataApiBaseUrl: string;
  metricsApiBaseUrl: string;
  controlApiBaseUrl: string;
  useMock: boolean;
  apiToken: string;
}

export interface EnvParseResult {
  config: AppConfig;
  issues: EnvIssue[];
  ok: boolean;
}

/**
 * Documented default origin per key. An UNSET *or EMPTY* value resolves to
 * these — "empty means the default", never "empty means use the empty
 * string" (bug #2). Mirrors `.env.example` and `client.ts`'s `baseFor()`.
 */
export const URL_DEFAULTS = {
  VITE_API_BASE_URL: "http://localhost:8602",
  VITE_DATA_API_BASE_URL: "http://localhost:8603",
  VITE_METRICS_API_BASE_URL: "http://localhost:8604",
  VITE_CONTROL_API_BASE_URL: "http://localhost:8601",
} as const;

export type UrlKey = keyof typeof URL_DEFAULTS;

/**
 * Accepted boolean spellings, case-insensitive. Deliberately a CLOSED set
 * with an explicit rejection path: anything outside it is an error rather
 * than a guess, because this one flag decides whether the app talks to a
 * live trading backend or renders fabricated mock data.
 */
const BOOL_TRUE = new Set(["true", "1", "yes", "on"]);
const BOOL_FALSE = new Set(["false", "0", "no", "off"]);
const BOOL_VOCABULARY = "true, false, 1, 0, yes, no, on, off";

/** The config the app falls back to when anything failed to validate. */
function safeFallbackConfig(): AppConfig {
  return {
    apiBaseUrl: URL_DEFAULTS.VITE_API_BASE_URL,
    dataApiBaseUrl: URL_DEFAULTS.VITE_DATA_API_BASE_URL,
    metricsApiBaseUrl: URL_DEFAULTS.VITE_METRICS_API_BASE_URL,
    controlApiBaseUrl: URL_DEFAULTS.VITE_CONTROL_API_BASE_URL,
    useMock: true,
    apiToken: "",
  };
}

/**
 * Validate one base-URL key.
 *
 * Returns the documented default (and records an error) on anything invalid,
 * so the caller always gets a usable string.
 */
function parseUrl(
  key: UrlKey,
  raw: string | undefined,
  issues: EnvIssue[]
): string {
  const fallback = URL_DEFAULTS[key];
  const trimmed = (raw ?? "").trim();
  // Present-but-empty is the bug-#2 case: treat it exactly like unset.
  if (trimmed === "") return fallback;

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    issues.push({
      key,
      severity: "error",
      message:
        `${key} must be an absolute http(s) URL (e.g. "${fallback}"). ` +
        `Got "${trimmed}", which is not a valid URL.`,
    });
    return fallback;
  }

  // A `javascript:` or `data:` URL parses perfectly well and would otherwise
  // be interpolated straight into fetch().
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    issues.push({
      key,
      severity: "error",
      message:
        `${key} must use the http: or https: protocol. Got "${parsed.protocol}" ` +
        `in "${trimmed}".`,
    });
    return fallback;
  }

  // Every call site builds requests as `${base}${path}`, so a query or
  // fragment on the base would swallow the path. A path PREFIX is fine and
  // supported (reverse-proxy single-origin deployments — see .env.example).
  if (parsed.search !== "" || parsed.hash !== "") {
    issues.push({
      key,
      severity: "error",
      message:
        `${key} must not contain a query string or fragment — request paths are ` +
        `appended directly to it, so "?"/"#" would swallow the path. Got "${trimmed}". ` +
        `A path prefix (e.g. "https://host.example.com/pilots") is allowed.`,
    });
    return fallback;
  }

  // Preserves the previous `.replace(/\/+$/, "")` behaviour exactly. Stripped
  // from the raw string (not `parsed.href`) so a bare origin stays
  // "http://host:8602" rather than becoming "http://host:8602/".
  return trimmed.replace(/\/+$/, "");
}

/** Validate `VITE_USE_MOCK` as a strict boolean. Falls back to `true` (mock). */
function parseUseMock(raw: string | undefined, issues: EnvIssue[]): boolean {
  const trimmed = (raw ?? "").trim().toLowerCase();
  // Unset/empty keeps the documented zero-config offline default.
  if (trimmed === "") return true;
  if (BOOL_TRUE.has(trimmed)) return true;
  if (BOOL_FALSE.has(trimmed)) return false;

  issues.push({
    key: "VITE_USE_MOCK",
    severity: "error",
    message:
      `VITE_USE_MOCK must be one of: ${BOOL_VOCABULARY} (case-insensitive). ` +
      `Got "${(raw ?? "").trim()}". Refusing to guess — this flag decides ` +
      `whether the app talks to your live trading backend.`,
  });
  return true;
}

/**
 * Token rules. Note these are NOT "require a token when remote" — that would
 * be exactly backwards for this codebase.
 *
 * `auth/apiToken.ts::getEffectiveToken()` deliberately IGNORES
 * `VITE_API_TOKEN` on a non-loopback origin (it's baked into the bundle and
 * therefore readable by anyone who loads the page); the token comes from
 * sessionStorage via <TokenGate> instead. So:
 *
 *  - non-loopback + live + token SET   -> warning: pure downside, a bundled
 *    secret that is also ignored at runtime.
 *  - non-loopback + live + token EMPTY -> no issue: <TokenGate> handles it.
 *  - loopback     + live + token EMPTY -> warning only. `api/auth.py`'s
 *    `require_read_token` is fail-open for loopback when `STATE_API_TOKEN`
 *    is unset, and `launch_webapp.command` GENERATES exactly this file
 *    (`VITE_API_TOKEN=` with an empty value) whenever `STATE_API_TOKEN`
 *    isn't set. Making it an error would break the launcher's own output.
 */
function checkToken(
  token: string,
  useMock: boolean,
  hostname: string | undefined,
  issues: EnvIssue[]
): void {
  // A build has no way to know which host it will be served from; guessing
  // would be fabrication, so skip all three checks.
  if (hostname === undefined) return;
  if (useMock) return;

  const isLoopback = LOOPBACK_HOSTNAMES.has(hostname);

  if (!isLoopback && token !== "") {
    issues.push({
      key: "VITE_API_TOKEN",
      severity: "warning",
      message:
        `VITE_API_TOKEN is set and this page is being served from a non-loopback ` +
        `host ("${hostname}"). The value is baked into the built JS bundle and can ` +
        `be read by anyone who loads the page — and src/auth/apiToken.ts ignores it ` +
        `on a non-loopback origin anyway (the token is entered once via TokenGate ` +
        `and kept in sessionStorage). Blank the VITE_API_TOKEN line before serving ` +
        `this build beyond localhost.`,
    });
    return;
  }

  if (isLoopback && token === "") {
    issues.push({
      key: "VITE_API_TOKEN",
      severity: "warning",
      message:
        `VITE_API_TOKEN is empty while running live against a loopback host. This is ` +
        `expected when the backend has no STATE_API_TOKEN set (read endpoints are ` +
        `fail-open for loopback requests), which is exactly what ` +
        `launch_webapp.command generates. If your backend DOES set STATE_API_TOKEN, ` +
        `set this to the same value or every request will 401.`,
    });
  }
}

/**
 * Validate a raw env bag into a usable config plus a list of issues.
 *
 * Pure — pass `import.meta.env` (or any plain object) in. Never throws.
 *
 * @param source  raw env bag, e.g. `import.meta.env`.
 * @param opts.hostname  the host the page is served from. Omit (undefined) to
 *   skip the token rules entirely, e.g. for a build-time/Node caller.
 */
export function parseEnv(
  source: Record<string, string | undefined>,
  opts?: { hostname?: string }
): EnvParseResult {
  const issues: EnvIssue[] = [];

  const candidate: AppConfig = {
    apiBaseUrl: parseUrl("VITE_API_BASE_URL", source.VITE_API_BASE_URL, issues),
    dataApiBaseUrl: parseUrl(
      "VITE_DATA_API_BASE_URL",
      source.VITE_DATA_API_BASE_URL,
      issues
    ),
    metricsApiBaseUrl: parseUrl(
      "VITE_METRICS_API_BASE_URL",
      source.VITE_METRICS_API_BASE_URL,
      issues
    ),
    controlApiBaseUrl: parseUrl(
      "VITE_CONTROL_API_BASE_URL",
      source.VITE_CONTROL_API_BASE_URL,
      issues
    ),
    useMock: parseUseMock(source.VITE_USE_MOCK, issues),
    apiToken: (source.VITE_API_TOKEN ?? "").trim(),
  };

  checkToken(candidate.apiToken, candidate.useMock, opts?.hostname, issues);

  const ok = issues.every((i) => i.severity !== "error");

  // On any error the app renders <EnvError/> instead of the router, so this
  // config is not really consumed — but it must still be whole. A HALF-applied
  // config (live base URL + mock token, say) is more dangerous than a clean
  // fallback, so an error collapses the whole thing to the safe defaults
  // rather than leaving some fields honoured and others not.
  return { config: ok ? candidate : safeFallbackConfig(), issues, ok };
}

// ── Impure edge ──────────────────────────────────────────────────────────────
// Evaluated once, as an import-time side effect. This has to happen HERE
// rather than as a runtime check inside main.tsx: ESM evaluates the entire
// import graph before main.tsx's own body executes, so api/client.ts's
// module-scope consts are already resolved by then. Validating at import time
// is the only point early enough to matter.
const result = parseEnv(
  import.meta.env as unknown as Record<string, string | undefined>,
  {
    hostname:
      typeof window !== "undefined" ? window.location.hostname : undefined,
  }
);

export const config = result.config;
export const envIssues = result.issues;
export const isEnvValid = result.ok;
