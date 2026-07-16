import { render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActivityFeed } from "./ActivityFeed";
import { api } from "../api/client";
import { theme } from "../theme";

describe("ActivityFeed component (R3)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  // T1.1: Renders Alerts List
  it("loads and displays alerts with severity levels", async () => {
    render(<ActivityFeed limit={5} />);
    expect(await screen.findByTestId("refresh-alerts-btn")).toBeInTheDocument();
    const cards = await screen.findAllByTestId("alert-card");
    expect(cards.length).toBeGreaterThan(0);
  });

  // T1.2: Refresh Event Dispatch
  it("triggers API refresh on button click", async () => {
    const spy = vi.spyOn(api, "getAlerts");
    render(<ActivityFeed limit={5} />);
    
    // First call is from mount
    expect(spy).toHaveBeenCalledTimes(1);

    const refreshBtn = await screen.findByTestId("refresh-alerts-btn");
    fireEvent.click(refreshBtn);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  // T1.3: Auto-Polling Interval
  it("polls alerts API every 10 seconds", async () => {
    const spy = vi.spyOn(api, "getAlerts");
    render(<ActivityFeed limit={5} />);
    expect(spy).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(spy).toHaveBeenCalledTimes(2);
  });

  // T1.4: Toggle Polling Switch
  it("halts polling when auto-poll checkbox is unchecked", async () => {
    const spy = vi.spyOn(api, "getAlerts");
    render(<ActivityFeed limit={5} />);
    const checkbox = await screen.findByTestId("toggle-polling-checkbox");
    
    // Uncheck polling
    fireEvent.click(checkbox);
    
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(spy).toHaveBeenCalledTimes(1); // Only mount fetch
  });

  // T1.5: Level Indicator Formatting
  it("formats CRITICAL alerts with the decline theme color", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: [{ timestamp: new Date().toISOString(), level: "CRITICAL", message: "Critical Volatility Event" }]
    });

    render(<ActivityFeed limit={1} />);
    const levelLabel = await screen.findByText("Critical");
    expect(levelLabel.style.color).toBe(theme.decline);
  });

  // T2.1: Debounce Refresh Operations
  it("debounces manual refresh button clicks to prevent parallel API requests", async () => {
    const spy = vi.spyOn(api, "getAlerts");
    render(<ActivityFeed limit={5} />);
    const refreshBtn = await screen.findByTestId("refresh-alerts-btn");
    
    fireEvent.click(refreshBtn);
    fireEvent.click(refreshBtn);
    fireEvent.click(refreshBtn);

    expect(spy).toHaveBeenCalledTimes(2); // 1 mount + 1 debounced manual click
  });

  // T2.2: Maintain Display During Fetch Failure
  it("keeps the last valid alerts visible on background polling failures", async () => {
    let callCount = 0;
    vi.spyOn(api, "getAlerts").mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({
          reason: null,
          entries: [{ timestamp: new Date().toISOString(), level: "INFO", message: "Initial alert" }]
        });
      }
      return Promise.reject(new Error("Network connection lost"));
    });

    render(<ActivityFeed limit={5} />);
    expect(await screen.findByText("Initial alert")).toBeInTheDocument();

    // Trigger background poll which fails
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    // Alert should still be visible on screen
    expect(screen.getByText("Initial alert")).toBeInTheDocument();
    expect(screen.queryByText("Network connection lost")).not.toBeInTheDocument();
  });

  // T2.3: Missing Level Category Values
  it("defaults missing level categories to INFO", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: [{ timestamp: new Date().toISOString(), level: null, message: "No Level alert" }]
    });

    render(<ActivityFeed limit={1} />);
    const levelLabel = await screen.findByText("Info");
    expect(levelLabel.style.color).toBe(theme.accent);
  });

  // T2.4: Limit Pagination Bounds
  it("applies container scroll constraint and virtual styles on large pagination lists (>100)", async () => {
    const largeEntries = Array.from({ length: 110 }).map((_, i) => ({
      timestamp: new Date().toISOString(),
      level: "INFO",
      message: `Alert #${i}`
    }));

    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: largeEntries
    });

    render(<ActivityFeed limit={150} />);
    
    const firstAlert = await screen.findByText("Alert #0");
    const container = firstAlert.closest("[data-testid='alert-card']")?.parentElement;
    expect(container).toBeInTheDocument();
    expect(container?.style.maxHeight).toBe("300px");
    expect(container?.style.overflowY).toBe("auto");
    expect(container?.style.contentVisibility).toBe("auto");
  });

  // T2.5: Cleanup Timers on Unmount
  it("clears polling intervals on unmount", async () => {
    const spy = vi.spyOn(api, "getAlerts");
    const { unmount } = render(<ActivityFeed limit={5} />);
    expect(spy).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(spy).toHaveBeenCalledTimes(1); // No additional poll
  });
});
