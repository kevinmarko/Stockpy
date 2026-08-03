/**
 * env.test.ts — table tests for the PURE `parseEnv()`.
 *
 * No `vi.stubEnv` / `vi.resetModules` needed anywhere in this file: that is
 * the whole reason parsing was factored into a pure function that takes the
 * env bag (and the hostname) as arguments instead of reaching for
 * `import.meta.env` / `window.location` itself.
 */
import { describe, expect, it } from "vitest";
import { parseEnv, URL_DEFAULTS, LOOPBACK_HOSTNAMES } from "./env";
import type { EnvIssue } from "./env";

/** All four base-URL keys paired with their documented default. */
const URL_KEYS = [
  ["VITE_API_BASE_URL", "apiBaseUrl", "http://localhost:8602"],
  ["VITE_DATA_API_BASE_URL", "dataApiBaseUrl", "http://localhost:8603"],
  ["VITE_METRICS_API_BASE_URL", "metricsApiBaseUrl", "http://localhost:8604"],
  ["VITE_CONTROL_API_BASE_URL", "controlApiBaseUrl", "http://localhost:8601"],
] as const;

const errorKeys = (issues: EnvIssue[]) =>
  issues.filter((i) => i.severity === "error").map((i) => i.key);
const warningKeys = (issues: EnvIssue[]) =>
  issues.filter((i) => i.severity === "warning").map((i) => i.key);

describe("parseEnv — base URLs", () => {
  it("an empty env bag yields every documented default and no issues", () => {
    const { config, issues, ok } = parseEnv({});
    expect(ok).toBe(true);
    expect(issues).toEqual([]);
    expect(config.apiBaseUrl).toBe("http://localhost:8602");
    expect(config.dataApiBaseUrl).toBe("http://localhost:8603");
    expect(config.metricsApiBaseUrl).toBe("http://localhost:8604");
    expect(config.controlApiBaseUrl).toBe("http://localhost:8601");
  });

  it.each(URL_KEYS)(
    "%s falls back to its default when unset",
    (key, field, expected) => {
      const { config, ok } = parseEnv({ [key]: undefined });
      expect(ok).toBe(true);
      expect(config[field]).toBe(expected);
    }
  );

  // ── Regression test for bug #2 ──────────────────────────────────────────
  // A present-but-empty value used to survive `?? default` (nullish-only) and
  // turn every fetch into a RELATIVE same-origin request.
  it.each(URL_KEYS)(
    "%s resolves an empty string to its default, never to \"\"",
    (key, field, expected) => {
      const { config, issues, ok } = parseEnv({ [key]: "" });
      expect(ok).toBe(true);
      expect(issues).toEqual([]);
      expect(config[field]).toBe(expected);
      expect(config[field]).not.toBe("");
    }
  );

  it.each(URL_KEYS)(
    "%s resolves a whitespace-only string to its default",
    (key, field, expected) => {
      const { config, ok } = parseEnv({ [key]: "   " });
      expect(ok).toBe(true);
      expect(config[field]).toBe(expected);
    }
  );

  it("strips trailing slashes (preserving the previous replace(/\\/+$/) behaviour)", () => {
    expect(
      parseEnv({ VITE_API_BASE_URL: "http://example.test:9000/" }).config
        .apiBaseUrl
    ).toBe("http://example.test:9000");
    expect(
      parseEnv({ VITE_API_BASE_URL: "http://example.test:9000///" }).config
        .apiBaseUrl
    ).toBe("http://example.test:9000");
  });

  it("accepts a path prefix (single-origin reverse-proxy deployments)", () => {
    const { config, issues, ok } = parseEnv({
      VITE_API_BASE_URL: "https://host.example.com/pilots",
    });
    expect(ok).toBe(true);
    expect(issues).toEqual([]);
    expect(config.apiBaseUrl).toBe("https://host.example.com/pilots");
  });

  it("accepts https and a path prefix with a trailing slash stripped", () => {
    expect(
      parseEnv({ VITE_DATA_API_BASE_URL: "https://host.example.com/data/" })
        .config.dataApiBaseUrl
    ).toBe("https://host.example.com/data");
  });

  it("rejects a javascript: URL (parses fine, would reach fetch())", () => {
    const { config, issues, ok } = parseEnv({
      VITE_API_BASE_URL: "javascript:alert(1)",
    });
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toContain("VITE_API_BASE_URL");
    expect(config.apiBaseUrl).toBe(URL_DEFAULTS.VITE_API_BASE_URL);
  });

  it("rejects a data: URL", () => {
    const { ok, issues } = parseEnv({
      VITE_API_BASE_URL: "data:text/html,<h1>x</h1>",
    });
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toContain("VITE_API_BASE_URL");
  });

  it("rejects a URL carrying a query string (it would swallow the path)", () => {
    const { ok, issues } = parseEnv({
      VITE_API_BASE_URL: "http://example.test:9000/?foo=1",
    });
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toContain("VITE_API_BASE_URL");
  });

  it("rejects a URL carrying a fragment", () => {
    const { ok, issues } = parseEnv({
      VITE_METRICS_API_BASE_URL: "http://example.test:9000/#frag",
    });
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toContain("VITE_METRICS_API_BASE_URL");
  });

  it("rejects an unparseable value", () => {
    const { ok, issues } = parseEnv({ VITE_API_BASE_URL: "not a url" });
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toContain("VITE_API_BASE_URL");
  });

  it("rejects a scheme-relative / host-only value with no protocol", () => {
    const { ok } = parseEnv({ VITE_API_BASE_URL: "example.test:9000" });
    expect(ok).toBe(false);
  });

  it("reports every bad URL key independently, not just the first", () => {
    const { issues, ok } = parseEnv({
      VITE_API_BASE_URL: "javascript:alert(1)",
      VITE_DATA_API_BASE_URL: "not a url",
      VITE_CONTROL_API_BASE_URL: "http://ok.test/?q=1",
    });
    expect(ok).toBe(false);
    expect(errorKeys(issues).sort()).toEqual([
      "VITE_API_BASE_URL",
      "VITE_CONTROL_API_BASE_URL",
      "VITE_DATA_API_BASE_URL",
    ]);
  });
});

describe("parseEnv — VITE_USE_MOCK strict boolean", () => {
  it("defaults to true (mock) when unset — the zero-config offline default", () => {
    expect(parseEnv({}).config.useMock).toBe(true);
  });

  it("defaults to true when empty or whitespace", () => {
    expect(parseEnv({ VITE_USE_MOCK: "" }).config.useMock).toBe(true);
    expect(parseEnv({ VITE_USE_MOCK: "  " }).config.useMock).toBe(true);
    expect(parseEnv({ VITE_USE_MOCK: "" }).ok).toBe(true);
  });

  const TRUTHY = ["true", "1", "yes", "on"];
  const FALSY = ["false", "0", "no", "off"];

  it.each(TRUTHY)("accepts %s as true (lower case)", (raw) => {
    const { config, ok } = parseEnv({ VITE_USE_MOCK: raw });
    expect(ok).toBe(true);
    expect(config.useMock).toBe(true);
  });

  it.each(TRUTHY.map((v) => v.toUpperCase()))(
    "accepts %s as true (upper case)",
    (raw) => {
      const { config, ok } = parseEnv({ VITE_USE_MOCK: raw });
      expect(ok).toBe(true);
      expect(config.useMock).toBe(true);
    }
  );

  it.each(FALSY)("accepts %s as false (lower case)", (raw) => {
    const { config, ok } = parseEnv({ VITE_USE_MOCK: raw });
    expect(ok).toBe(true);
    expect(config.useMock).toBe(false);
  });

  it.each(FALSY.map((v) => v.toUpperCase()))(
    "accepts %s as false (upper case)",
    (raw) => {
      const { config, ok } = parseEnv({ VITE_USE_MOCK: raw });
      expect(ok).toBe(true);
      expect(config.useMock).toBe(false);
    }
  );

  // ── Regression test for bug #1 ──────────────────────────────────────────
  // `(raw ?? "true").toLowerCase() !== "false"` left "0" meaning MOCK, so an
  // operator who wrote VITE_USE_MOCK=0 to go live silently kept seeing
  // fabricated data.
  it('VITE_USE_MOCK=0 means LIVE, not mock (was: silently mock)', () => {
    const { config, ok } = parseEnv({ VITE_USE_MOCK: "0" });
    expect(ok).toBe(true);
    expect(config.useMock).toBe(false);
  });

  it("tolerates surrounding whitespace around a valid literal", () => {
    expect(parseEnv({ VITE_USE_MOCK: "  false  " }).config.useMock).toBe(false);
  });

  it("rejects an unrecognised value with an error naming the key", () => {
    const { issues, ok } = parseEnv({ VITE_USE_MOCK: "maybe" });
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toEqual(["VITE_USE_MOCK"]);
    const msg = issues[0].message;
    expect(msg).toContain("VITE_USE_MOCK");
    expect(msg).toContain("maybe");
    expect(msg).toContain("Refusing to guess");
  });

  it("rejects a near-miss typo such as flase", () => {
    expect(parseEnv({ VITE_USE_MOCK: "flase" }).ok).toBe(false);
  });
});

describe("parseEnv — API token rules", () => {
  const LOCAL = "localhost";
  const REMOTE = "192.168.1.5";

  it("exports the same three loopback hostnames apiToken.ts used to define", () => {
    expect([...LOOPBACK_HOSTNAMES].sort()).toEqual([
      "127.0.0.1",
      "::1",
      "localhost",
    ]);
  });

  it("warns when a token is set on a NON-loopback host in live mode (bundled + ignored)", () => {
    const { issues, ok } = parseEnv(
      { VITE_USE_MOCK: "false", VITE_API_TOKEN: "s3cret" },
      { hostname: REMOTE }
    );
    // A warning must never block startup.
    expect(ok).toBe(true);
    expect(warningKeys(issues)).toEqual(["VITE_API_TOKEN"]);
    expect(issues[0].message).toContain("apiToken.ts");
  });

  it("raises NO issue when a non-loopback host in live mode has an empty token (TokenGate handles it)", () => {
    const { issues, ok } = parseEnv(
      { VITE_USE_MOCK: "false", VITE_API_TOKEN: "" },
      { hostname: REMOTE }
    );
    expect(ok).toBe(true);
    expect(issues).toEqual([]);
  });

  it("raises NO issue when a non-loopback host in live mode omits the token entirely", () => {
    const { issues } = parseEnv(
      { VITE_USE_MOCK: "false" },
      { hostname: REMOTE }
    );
    expect(issues).toEqual([]);
  });

  // This is precisely what launch_webapp.command generates when the backend
  // has no STATE_API_TOKEN. It must stay a WARNING — an error here would
  // break the launcher's own output.
  it("warns (never errors) on an empty token on loopback in live mode", () => {
    const { issues, ok } = parseEnv(
      { VITE_USE_MOCK: "false", VITE_API_TOKEN: "" },
      { hostname: LOCAL }
    );
    expect(ok).toBe(true);
    expect(errorKeys(issues)).toEqual([]);
    expect(warningKeys(issues)).toEqual(["VITE_API_TOKEN"]);
  });

  it.each(["localhost", "127.0.0.1", "::1"])(
    "treats %s as loopback",
    (hostname) => {
      const { issues } = parseEnv(
        { VITE_USE_MOCK: "false", VITE_API_TOKEN: "s3cret" },
        { hostname }
      );
      // A token on loopback is the normal, intended case — no issue at all.
      expect(issues).toEqual([]);
    }
  );

  it("raises no token issue on loopback in live mode with a token set", () => {
    const { issues, ok } = parseEnv(
      { VITE_USE_MOCK: "false", VITE_API_TOKEN: "s3cret" },
      { hostname: LOCAL }
    );
    expect(ok).toBe(true);
    expect(issues).toEqual([]);
  });

  it("skips all token checks in mock mode, on either host", () => {
    expect(
      parseEnv({ VITE_USE_MOCK: "true" }, { hostname: LOCAL }).issues
    ).toEqual([]);
    expect(
      parseEnv(
        { VITE_USE_MOCK: "true", VITE_API_TOKEN: "s3cret" },
        { hostname: REMOTE }
      ).issues
    ).toEqual([]);
  });

  it("skips all three token checks when hostname is undefined (build-time caller)", () => {
    // Each of these would produce an issue if a hostname were supplied.
    expect(
      parseEnv({ VITE_USE_MOCK: "false", VITE_API_TOKEN: "s3cret" }).issues
    ).toEqual([]);
    expect(
      parseEnv({ VITE_USE_MOCK: "false", VITE_API_TOKEN: "" }).issues
    ).toEqual([]);
    expect(parseEnv({ VITE_USE_MOCK: "false" }, {}).issues).toEqual([]);
  });

  it("trims the token", () => {
    expect(parseEnv({ VITE_API_TOKEN: "  abc  " }).config.apiToken).toBe("abc");
  });

  it("defaults the token to an empty string when unset", () => {
    expect(parseEnv({}).config.apiToken).toBe("");
  });
});

/**
 * The repo's own launcher writes webapp/.env.local in live mode. Its exact
 * output must never trip an ERROR, or `./launch_webapp.command` would ship a
 * config the app then refuses to start on. Pinned here so a future tightening
 * of these rules can't silently break the launcher.
 *
 * Source: launch_webapp.command, the `_read_env_value STATE_API_TOKEN` block.
 */
describe("parseEnv — launch_webapp.command's generated .env.local", () => {
  const LAUNCHER_ENV = {
    VITE_USE_MOCK: "false",
    VITE_API_BASE_URL: "http://localhost:8602",
    VITE_DATA_API_BASE_URL: "http://localhost:8603",
    VITE_METRICS_API_BASE_URL: "http://localhost:8604",
    VITE_CONTROL_API_BASE_URL: "http://localhost:8601",
  };

  it("with STATE_API_TOKEN unset (VITE_API_TOKEN=) — no errors, one warning", () => {
    const { config, issues, ok } = parseEnv(
      { ...LAUNCHER_ENV, VITE_API_TOKEN: "" },
      { hostname: "localhost" }
    );
    expect(ok).toBe(true);
    expect(errorKeys(issues)).toEqual([]);
    expect(warningKeys(issues)).toEqual(["VITE_API_TOKEN"]);
    expect(config.useMock).toBe(false);
    expect(config.apiBaseUrl).toBe("http://localhost:8602");
    expect(config.controlApiBaseUrl).toBe("http://localhost:8601");
  });

  it("with STATE_API_TOKEN set — no issues at all", () => {
    const { config, issues, ok } = parseEnv(
      { ...LAUNCHER_ENV, VITE_API_TOKEN: "abc123" },
      { hostname: "localhost" }
    );
    expect(ok).toBe(true);
    expect(issues).toEqual([]);
    expect(config.useMock).toBe(false);
    expect(config.apiToken).toBe("abc123");
  });
});

describe("parseEnv — result shape guarantees", () => {
  it("ok mirrors the absence of error-severity issues", () => {
    const warnOnly = parseEnv(
      { VITE_USE_MOCK: "false", VITE_API_TOKEN: "" },
      { hostname: "localhost" }
    );
    expect(warnOnly.issues.length).toBe(1);
    expect(warnOnly.ok).toBe(true);
    expect(warnOnly.ok).toBe(
      warnOnly.issues.every((i) => i.severity !== "error")
    );
  });

  it("returns a FULLY-FORMED safe-fallback config when ok is false — never partial", () => {
    // A live base URL paired with a mock-mode token would be worse than a
    // clean fallback, so an error collapses the WHOLE config to defaults.
    const { config, ok } = parseEnv({
      VITE_API_BASE_URL: "https://real-backend.example.com",
      VITE_USE_MOCK: "maybe",
      VITE_API_TOKEN: "s3cret",
    });

    expect(ok).toBe(false);
    expect(config).toBeDefined();
    expect(config.useMock).toBe(true);
    expect(config.apiToken).toBe("");
    expect(config.apiBaseUrl).toBe(URL_DEFAULTS.VITE_API_BASE_URL);
    expect(config.dataApiBaseUrl).toBe(URL_DEFAULTS.VITE_DATA_API_BASE_URL);
    expect(config.metricsApiBaseUrl).toBe(
      URL_DEFAULTS.VITE_METRICS_API_BASE_URL
    );
    expect(config.controlApiBaseUrl).toBe(
      URL_DEFAULTS.VITE_CONTROL_API_BASE_URL
    );
    // Every field present and a usable string/boolean — no undefined leaks.
    for (const value of Object.values(config)) {
      expect(value).not.toBeUndefined();
    }
  });

  it("never throws, whatever junk it is handed", () => {
    expect(() =>
      parseEnv(
        {
          VITE_API_BASE_URL: "://///",
          VITE_DATA_API_BASE_URL: "javascript:void 0",
          VITE_METRICS_API_BASE_URL: "   ",
          VITE_CONTROL_API_BASE_URL: "http://x.test/?a#b",
          VITE_USE_MOCK: "¯\\_(ツ)_/¯",
          VITE_API_TOKEN: "  ",
        },
        { hostname: "example.com" }
      )
    ).not.toThrow();
  });

  it("ignores unknown VITE_* keys rather than complaining about them", () => {
    const { ok, issues } = parseEnv({ VITE_SOMETHING_ELSE: "whatever" });
    expect(ok).toBe(true);
    expect(issues).toEqual([]);
  });

  /**
   * The literal object Vite bakes in for `import.meta.env` — captured from a
   * real `vite build` with `.env.local` containing `VITE_USE_MOCK=maybe` and a
   * bare `VITE_API_BASE_URL=`. Note `VITE_API_BASE_URL:""`: a PRESENT, EMPTY
   * string, which is precisely why `?? default` (nullish-only) could not catch
   * bug #2. Pinned so the parser is proven against the real production shape,
   * extra non-VITE keys and all.
   */
  it("handles the real Vite-baked import.meta.env shape", () => {
    const { config, issues, ok } = parseEnv(
      {
        BASE_URL: "/",
        DEV: false as unknown as string,
        MODE: "production",
        PROD: true as unknown as string,
        SSR: false as unknown as string,
        VITE_API_BASE_URL: "",
        VITE_USE_MOCK: "maybe",
      },
      { hostname: "localhost" }
    );

    // The bad boolean is the only error; the non-VITE keys are ignored.
    expect(ok).toBe(false);
    expect(errorKeys(issues)).toEqual(["VITE_USE_MOCK"]);
    // ...and the empty base URL resolves to the documented default, never "".
    expect(config.apiBaseUrl).toBe("http://localhost:8602");
  });
});
