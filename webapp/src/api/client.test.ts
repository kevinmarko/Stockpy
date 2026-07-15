/**
 * client.test.ts — offline tests for the LIVE client (liveApi inside client.ts),
 * the code path that actually talks to `api/pilots_api.py` in production.
 *
 * `USE_MOCK`/`BASE_URL`/`TOKEN` are read from `import.meta.env` at module
 * top-level, so each test stubs the env with `vi.stubEnv` and re-imports the
 * module fresh (`vi.resetModules`) to force those consts to re-evaluate against
 * the live branch. `global.fetch` is mocked — no network, no running backend.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function importLiveClient(env: Record<string, string> = {}) {
  vi.stubEnv("VITE_USE_MOCK", "false");
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  vi.resetModules();
  return import("./client");
}

function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? (status >= 200 && status < 300),
    status,
    statusText: "",
    json: async () => body,
  } as Response;
}

describe("client.ts — live client (mocked fetch)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("USE_MOCK is false and apiMeta reflects it once VITE_USE_MOCK=false", async () => {
    const mod = await importLiveClient();
    expect(mod.USE_MOCK).toBe(false);
    expect(mod.apiMeta.useMock).toBe(false);
  });

  it("defaults to http://localhost:8602 with no VITE_API_BASE_URL set", async () => {
    const mod = await importLiveClient();
    expect(mod.apiMeta.baseUrl).toBe("http://localhost:8602");
  });

  it("strips a trailing slash from a configured VITE_API_BASE_URL", async () => {
    const mod = await importLiveClient({ VITE_API_BASE_URL: "http://example.test:9000/" });
    expect(mod.apiMeta.baseUrl).toBe("http://example.test:9000");
  });

  it("listPilots() calls GET /pilots against the configured base URL, no auth header when no token", async () => {
    const mod = await importLiveClient({ VITE_API_BASE_URL: "http://example.test:9000" });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(jsonResponse([{ id: "trend-following" }]));

    const result = await mod.api.listPilots();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://example.test:9000/pilots");
    expect((init.headers as Record<string, string>)["Authorization"]).toBeUndefined();
    expect(result).toEqual([{ id: "trend-following" }]);
  });

  it("attaches Authorization: Bearer <token> when VITE_API_TOKEN is set", async () => {
    const mod = await importLiveClient({ VITE_API_TOKEN: "secret-token" });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ok" }));

    await mod.api.health();

    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe(
      "Bearer secret-token"
    );
  });

  it("apiMeta.hasToken is false when VITE_API_TOKEN is unset", async () => {
    const mod = await importLiveClient();
    expect(mod.apiMeta.hasToken).toBe(false);
  });

  it("POST follow() sends a JSON body with Content-Type set", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ mode: "off", queue_written: false, planned_intents: [] })
    );

    await mod.api.follow("trend-following", 500);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8602/pilots/trend-following/follow");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json"
    );
    expect(JSON.parse(init.body as string)).toEqual({ amount: 500 });
  });

  it("a non-OK response with a JSON {detail} body raises ApiError with that message + status", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Pilot 'nope' not found" }, { status: 404, ok: false })
    );

    await expect(mod.api.getPilot("nope")).rejects.toMatchObject({
      status: 404,
      message: "Pilot 'nope' not found",
    });
  });

  it("a non-OK response with a non-JSON body falls back to '<status> <statusText>'", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => {
        throw new Error("not json");
      },
    } as unknown as Response);

    await expect(mod.api.getFollows()).rejects.toMatchObject({
      status: 500,
      message: "500 Internal Server Error",
    });
  });

  it("a network failure (fetch throws) raises ApiError(status=0) naming the base URL", async () => {
    const mod = await importLiveClient({ VITE_API_BASE_URL: "http://example.test:9000" });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockRejectedValue(new TypeError("network down"));

    await expect(mod.api.listPilots()).rejects.toMatchObject({ status: 0 });
    await expect(mod.api.listPilots()).rejects.toThrow(/example\.test:9000/);
  });

  it("a 204 response resolves to undefined instead of parsing an empty body", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: "",
      json: async () => {
        throw new Error("should not be called on 204");
      },
    } as unknown as Response);

    const result = await mod.api.getEquityCurve("1M");
    expect(result).toBeUndefined();
  });

  it("useMock=true (default) never touches fetch — mock and live are mutually exclusive", async () => {
    vi.resetModules();
    vi.unstubAllEnvs();
    const mod = await import("./client");
    expect(mod.USE_MOCK).toBe(true);
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await mod.api.listPilots();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("getBrokerageStatus() calls GET /brokerage/status", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ connected: false, has_account_snapshot: false })
    );

    const result = await mod.api.getBrokerageStatus();

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8602/brokerage/status");
    expect(result).toEqual({ connected: false, has_account_snapshot: false });
  });

  it("connectBrokerage() POSTs credentials as JSON to /brokerage/connect", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ connected: true, verified: true, has_account_snapshot: false })
    );

    await mod.api.connectBrokerage({
      username: "user@example.com",
      password: "hunter2",
      mfa_secret: "SECRET",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8602/brokerage/connect");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      username: "user@example.com",
      password: "hunter2",
      mfa_secret: "SECRET",
    });
  });

  it("connectBrokerage() surfaces a 401 verification failure as an ApiError", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: "Could not verify Robinhood credentials." },
        { status: 401, ok: false }
      )
    );

    await expect(
      mod.api.connectBrokerage({ username: "u", password: "wrong", mfa_secret: "s" })
    ).rejects.toMatchObject({
      status: 401,
      message: "Could not verify Robinhood credentials.",
    });
  });

  it("disconnectBrokerage() POSTs to /brokerage/disconnect", async () => {
    const mod = await importLiveClient();
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(jsonResponse({ connected: false }));

    const result = await mod.api.disconnectBrokerage();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8602/brokerage/disconnect");
    expect(init.method).toBe("POST");
    expect(result).toEqual({ connected: false });
  });
});
