/**
 * LogStream.test.tsx — the SSE log console used by Console.tsx.
 *
 * Two describe blocks:
 *  - "mock mode" exercises the placeholder/static controls under the test
 *    environment's real USE_MOCK=true.
 *  - "live streaming (simulated)" mocks api/client's USE_MOCK to false and
 *    installs a fake global EventSource, to guard two bugs found during
 *    manual live verification against a real backend that this repo's own
 *    "no half-finished implementations" bar requires covering:
 *      1. logs from a previous job bleeding into a newly-started job's view
 *         (state was never reset on jobId change), and
 *      2. auto-scroll calling scrollIntoView(), which bubbles through every
 *         scrollable ancestor including the outer page -- confirmed live to
 *         carry the whole viewport away from the Console screen instead of
 *         staying within this panel's own scrollable list.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("LogStream (mock mode)", () => {
  it("shows the mock-mode placeholder instead of attempting a live connection", async () => {
    const { LogStream } = await import("./LogStream");
    render(<LogStream jobId="job-1" isStreaming />);
    expect(
      screen.getByText("Log streaming is only available in live mode.")
    ).toBeInTheDocument();
  });

  it("renders the filter input, auto-scroll toggle, and clear button", async () => {
    const { LogStream } = await import("./LogStream");
    render(<LogStream jobId="job-1" isStreaming={false} />);
    expect(screen.getByPlaceholderText("Filter logs...")).toBeInTheDocument();
    expect(screen.getByText("Auto-scroll")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("renders the idle state when no job is active", async () => {
    const { LogStream } = await import("./LogStream");
    render(<LogStream />);
    expect(
      screen.getByText("Log streaming is only available in live mode.")
    ).toBeInTheDocument();
  });
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners: Record<string, Array<() => void>> = {};

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, cb: () => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  close() {
    this.closed = true;
  }
  emit(data: string) {
    this.onmessage?.({ data });
  }
}

describe("LogStream (live streaming, simulated EventSource)", () => {
  beforeEach(() => {
    vi.resetModules();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return {
        ...actual,
        USE_MOCK: false,
        jobStreamUrl: (jobId: string) => `http://test.invalid/jobs/${jobId}/stream`,
      };
    });
  });

  afterEach(() => {
    vi.doUnmock("../api/client");
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("clears the previous job's log lines when a new job starts", async () => {
    const { LogStream } = await import("./LogStream");
    const { rerender } = render(<LogStream jobId="job-a" isStreaming />);
    FakeEventSource.instances[0].emit("line from job a");
    expect(await screen.findByText("line from job a")).toBeInTheDocument();

    rerender(<LogStream jobId="job-b" isStreaming />);

    expect(screen.queryByText("line from job a")).not.toBeInTheDocument();
    expect(screen.getByText("No logs received yet...")).toBeInTheDocument();
  });

  it("auto-scrolls only its own list container, never the page (no scrollIntoView)", async () => {
    const scrollIntoViewSpy = vi.fn();
    // jsdom implements no real layout, so Element.prototype.scrollIntoView is
    // itself a no-op by default -- stub it explicitly so a regression
    // (reintroducing scrollIntoView, which bubbles to the outer page) would
    // be caught here instead of only in manual live verification.
    Element.prototype.scrollIntoView = scrollIntoViewSpy;

    const { LogStream } = await import("./LogStream");
    render(<LogStream jobId="job-a" isStreaming />);
    FakeEventSource.instances[0].emit("a log line");
    await screen.findByText("a log line");

    expect(scrollIntoViewSpy).not.toHaveBeenCalled();
  });

  it("caps the log buffer to MAX_LOG_LINES to prevent unbounded memory growth", async () => {
    const { LogStream, MAX_LOG_LINES } = await import("./LogStream");
    render(<LogStream jobId="job-capped" isStreaming />);
    const es = FakeEventSource.instances[0];

    // Emit more than MAX_LOG_LINES
    for (let i = 1; i <= MAX_LOG_LINES + 5; i++) {
      es.emit(`log entry ${i}`);
    }

    // First 5 entries should have slid out of buffer
    expect(screen.queryByText("log entry 1")).not.toBeInTheDocument();
    expect(screen.queryByText("log entry 5")).not.toBeInTheDocument();
    // Latest entry should be present
    expect(await screen.findByText(`log entry ${MAX_LOG_LINES + 5}`)).toBeInTheDocument();
  });
});
