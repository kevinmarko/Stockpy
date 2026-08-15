import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveTick } from "./useLiveTick";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  readyState = 0;
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
    this.readyState = 3;
    if (this.onclose) {
      this.onclose();
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

  emitClose() {
    this.readyState = 3;
    this.closed = true;
    this.onclose?.();
  }
}

describe("useLiveTick", () => {
  beforeEach(() => {
    vi.useFakeTimers();
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
        source: "alpaca",
        is_stale: false,
      });
    });

    expect(result.current.isConnected).toBe(true);
    expect(result.current.price).toBe(185.5);
    expect(result.current.bid).toBe(185.45);
    expect(result.current.ask).toBe(185.55);
    expect(result.current.source).toBe("alpaca");
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
});
