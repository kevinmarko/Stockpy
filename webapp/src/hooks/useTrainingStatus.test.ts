import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTrainingStatus } from "./useTrainingStatus";

// Vitest runs in mock mode (.env.test); USE_MOCK is a getter over a hoisted
// flag so these tests can exercise both the live and the mock path.
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
  closed = false;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
    this.onclose?.({ code: 1005 });
  }

  emitOpen() {
    this.onopen?.();
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }

  emitClose(code = 1006) {
    this.closed = true;
    this.onclose?.({ code });
  }
}

const latest = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

describe("useTrainingStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mode.mock = false;
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("connects to /ws/training/status and merges job frames by job_id", () => {
    const { result } = renderHook(() => useTrainingStatus());
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(latest().url).toContain("/ws/training/status");

    act(() => {
      latest().emitOpen();
      latest().emitMessage({ job_id: "a", status: "started" });
      latest().emitMessage({ job_id: "b", status: "finished", exit_code: 0 });
    });
    expect(result.current).toEqual({
      a: { status: "started", exit_code: null },
      b: { status: "finished", exit_code: 0 },
    });
  });

  it("backs off 1 s -> 30 s cap while the socket never opens", () => {
    renderHook(() => useTrainingStatus());
    for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
      const before = FakeWebSocket.instances.length;
      act(() => latest().emitClose(1006));
      act(() => vi.advanceTimersByTime(delay - 1));
      expect(FakeWebSocket.instances).toHaveLength(before);
      act(() => vi.advanceTimersByTime(1));
      expect(FakeWebSocket.instances).toHaveLength(before + 1);
    }
  });

  it("opens at most a handful of sockets in 10 s against a refusing server", () => {
    // The 2026-10-02 symptom: several rejected handshakes per second.
    renderHook(() => useTrainingStatus());
    for (let t = 0; t < 10_000; t += 100) {
      act(() => {
        if (!latest().closed) latest().emitClose(1006);
        vi.advanceTimersByTime(100);
      });
    }
    expect(FakeWebSocket.instances.length).toBeLessThanOrEqual(4);
  });

  it.each([4003, 4001, 1008])("stops retrying after an auth rejection (close %i)", (code) => {
    renderHook(() => useTrainingStatus());
    act(() => latest().emitClose(code));
    act(() => vi.advanceTimersByTime(300_000));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it("does not reconnect after unmount", () => {
    const { unmount } = renderHook(() => useTrainingStatus());
    unmount();
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].closed).toBe(true);
  });

  it("opens no WebSocket in mock mode", () => {
    mode.mock = true;
    const { result } = renderHook(() => useTrainingStatus());
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(result.current).toEqual({});
  });
});
