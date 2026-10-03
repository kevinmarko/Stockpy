import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveTick } from "./useLiveTick";

// Vitest runs in mock mode (.env.test), where useLiveTick deliberately opens
// no socket. These tests exercise the live path, so USE_MOCK is a getter over
// a hoisted flag each test can flip.
const mode = vi.hoisted(() => ({ mock: false }));
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    get USE_MOCK() {
      return mode.mock;
    },
  };
});

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  readyState = 0;
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({ code: 1005 });
    }
  }

  emitOpen() {
    this.readyState = 1;
    this.onopen?.();
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  emitError() {
    this.onerror?.();
  }

  emitClose(code = 1006) {
    this.readyState = 3;
    this.closed = true;
    this.onclose?.({ code });
  }
}

describe("useLiveTick", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mode.mock = false;
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("initializes with default connecting tick and creates a WebSocket", () => {
    const { result } = renderHook(() => useLiveTick("AAPL"));

    expect(result.current.symbol).toBe("AAPL");
    expect(result.current.isConnected).toBe(false);
    expect(result.current.source).toBe("connecting");
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain("/ws/ticks/AAPL");
  });

  it("updates price and connection status upon receiving a tick frame", () => {
    const { result } = renderHook(() => useLiveTick("AAPL"));
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.emitOpen();
      ws.emitMessage({
        symbol: "AAPL",
        price: 185.5,
        bid: 185.45,
        ask: 185.55,
        source: "fmp",
        is_stale: false,
      });
    });

    expect(result.current.isConnected).toBe(true);
    expect(result.current.price).toBe(185.5);
    expect(result.current.bid).toBe(185.45);
    expect(result.current.ask).toBe(185.55);
    expect(result.current.source).toBe("fmp");
    expect(result.current.isStale).toBe(false);
  });

  it("handles frame errors gracefully", () => {
    const { result } = renderHook(() => useLiveTick("AAPL"));
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.emitOpen();
      ws.emitMessage({ error: "Rate limit exceeded" });
    });

    expect(result.current.error).toBe("Rate limit exceeded");
  });

  it("handles socket error", () => {
    const { result } = renderHook(() => useLiveTick("AAPL"));
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.emitError();
    });

    expect(result.current.error).toBe("WebSocket error");
    expect(result.current.isConnected).toBe(false);
  });

  it("cleans up WebSocket on unmount and prevents further reconnects", () => {
    const { unmount } = renderHook(() => useLiveTick("AAPL"));
    expect(FakeWebSocket.instances).toHaveLength(1);
    const ws = FakeWebSocket.instances[0];

    unmount();
    expect(ws.closed).toBe(true);

    // Advance time past any reconnect delay
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    // Should NOT have created a second socket
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("reconnects with backoff when connection closes while mounted", () => {
    renderHook(() => useLiveTick("AAPL"));
    expect(FakeWebSocket.instances).toHaveLength(1);
    const ws = FakeWebSocket.instances[0];

    act(() => {
      ws.emitClose();
    });

    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("backs off exponentially to a 30 s cap while the socket never opens", () => {
    renderHook(() => useLiveTick("AAPL"));
    const expected = [1000, 2000, 4000, 8000, 16000, 30000, 30000];
    for (const delay of expected) {
      const before = FakeWebSocket.instances.length;
      act(() => {
        FakeWebSocket.instances[before - 1].emitClose(1006);
      });
      act(() => {
        vi.advanceTimersByTime(delay - 1);
      });
      expect(FakeWebSocket.instances).toHaveLength(before);
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(FakeWebSocket.instances).toHaveLength(before + 1);
    }
  });

  it("resets the backoff once a socket actually opens", () => {
    renderHook(() => useLiveTick("AAPL"));
    act(() => FakeWebSocket.instances[0].emitClose(1006));
    act(() => vi.advanceTimersByTime(1000));
    act(() => FakeWebSocket.instances[1].emitClose(1006));
    act(() => vi.advanceTimersByTime(2000));
    act(() => {
      FakeWebSocket.instances[2].emitOpen();
      FakeWebSocket.instances[2].emitClose(1006);
    });
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeWebSocket.instances).toHaveLength(4);
  });

  it.each([4003, 4001, 1008])("stops retrying after an auth rejection (close %i)", (code) => {
    const { result } = renderHook(() => useLiveTick("AAPL"));
    act(() => FakeWebSocket.instances[0].emitClose(code));
    act(() => vi.advanceTimersByTime(120_000));

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.source).toBe("unauthorized");
    expect(result.current.price).toBeNull();
    expect(result.current.error).toContain(String(code));
  });

  it("opens no WebSocket in mock mode and reports no price", () => {
    mode.mock = true;
    const { result } = renderHook(() => useLiveTick("AAPL"));
    act(() => vi.advanceTimersByTime(60_000));

    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(result.current.source).toBe("mock");
    expect(result.current.price).toBeNull();
    expect(result.current.isConnected).toBe(false);
  });
});
