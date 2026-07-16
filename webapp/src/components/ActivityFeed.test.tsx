/**
 * ActivityFeed.test.tsx — the shared alerts-feed widget. Exercises the frozen
 * signature ({ limit, pilotIds, pollIntervalMs }) and, above all, the HONESTY
 * invariants: no fabricated severity for a null level, the feed's real `reason`
 * surfaced verbatim on an empty feed, exact-match-only pilotId attribution (no
 * message-text matching), and the 404-vs-hard-error ErrorState split.
 *
 * `api` is already the mock (VITE_USE_MOCK default-true) — we never vi.mock the
 * module; we spy on api.getAlerts only for the error / edge fixtures.
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActivityFeed } from "./ActivityFeed";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { theme } from "../theme";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** Normalize a CSS color through the DOM so a hex compares equal to jsdom's rgb(). */
function normColor(c: string): string {
  const el = document.createElement("span");
  el.style.color = c;
  return el.style.color;
}

describe("ActivityFeed — rendering & honesty", () => {
  it("loads and displays alert cards from the real mock feed", async () => {
    render(<ActivityFeed limit={5} />);
    const cards = await screen.findAllByTestId("alert-card");
    expect(cards.length).toBeGreaterThan(0);
  });

  it("formats a CRITICAL alert with the decline theme color", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: [
        {
          timestamp: new Date().toISOString(),
          level: "CRITICAL",
          message: "Critical volatility event",
          extra: null,
        },
      ],
    });
    render(<ActivityFeed limit={1} />);
    const label = await screen.findByText("Critical");
    expect(label.style.color).toBe(normColor(theme.decline));
  });

  it("renders '—' for a null level and NEVER a fabricated 'Info'", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: [
        {
          timestamp: new Date().toISOString(),
          level: null,
          message: "No level alert",
          extra: null,
        },
      ],
    });
    render(<ActivityFeed limit={1} />);
    expect(await screen.findByText("No level alert")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("Info")).not.toBeInTheDocument();
  });

  it("shows the feed's real `reason` verbatim on an empty feed, not 'No alerts yet.'", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      entries: [],
      reason: "Alert file not configured (set ALERT_FILE_PATH to enable).",
    });
    render(<ActivityFeed limit={5} />);
    expect(
      await screen.findByText(/Alert file not configured/)
    ).toBeInTheDocument();
    expect(screen.queryByText("No alerts yet.")).not.toBeInTheDocument();
  });
});

describe("ActivityFeed — pilotIds exact-match attribution", () => {
  it("matches ONLY on extra.pilot_id, never on message text", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: [
        {
          timestamp: new Date().toISOString(),
          level: "INFO",
          message: "Attributed alert",
          extra: { pilot_id: "trend-following" },
        },
        {
          // Message MENTIONS a pilot by name but carries no extra.pilot_id — it
          // must NOT be attributed (this is the facade the fix removes).
          timestamp: new Date().toISOString(),
          level: "INFO",
          message: "The trend follower fired a signal",
          extra: { type: "signal" },
        },
      ],
    });
    render(<ActivityFeed pilotIds={["trend-following"]} />);
    expect(await screen.findByText("Attributed alert")).toBeInTheDocument();
    expect(
      screen.queryByText("The trend follower fired a signal")
    ).not.toBeInTheDocument();
  });
});

describe("ActivityFeed — error vs cold-start", () => {
  it("surfaces ErrorState with a Retry on a hard error, and retry reloads", async () => {
    const spy = vi
      .spyOn(api, "getAlerts")
      .mockRejectedValueOnce(new ApiError("boom", 500));

    render(<ActivityFeed />);

    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();

    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeInTheDocument();

    // Next call falls through to the real mock (4 entries) → error clears.
    retry.click();
    await waitFor(() =>
      expect(screen.queryByText("Couldn't load")).not.toBeInTheDocument()
    );
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("renders the cold-start copy with NO Retry button on a 404", async () => {
    vi.spyOn(api, "getAlerts").mockRejectedValueOnce(new ApiError("nope", 404));
    render(<ActivityFeed />);
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry" })
    ).not.toBeInTheDocument();
  });
});

describe("ActivityFeed — refresh & polling", () => {
  it("triggers a fetch on the manual Refresh button", async () => {
    vi.useFakeTimers();
    const spy = vi
      .spyOn(api, "getAlerts")
      .mockResolvedValue({ entries: [], reason: null });

    render(<ActivityFeed limit={5} />);
    expect(spy).toHaveBeenCalledTimes(1); // mount
    await act(async () => {}); // flush the mount fetch so the in-flight guard clears

    fireEvent.click(screen.getByTestId("refresh-alerts-btn"));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("halts polling when the auto-poll checkbox is unchecked", async () => {
    vi.useFakeTimers();
    const spy = vi
      .spyOn(api, "getAlerts")
      .mockResolvedValue({ entries: [], reason: null });

    render(<ActivityFeed limit={5} pollIntervalMs={10000} />);
    await act(async () => {});
    expect(spy).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("toggle-polling-checkbox")); // uncheck
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(spy).toHaveBeenCalledTimes(1); // no background poll fired
  });

  it("polls on the configured pollIntervalMs cadence (not a hardcoded interval)", async () => {
    vi.useFakeTimers();
    const spy = vi
      .spyOn(api, "getAlerts")
      .mockResolvedValue({ entries: [], reason: null });

    render(<ActivityFeed pollIntervalMs={15000} />);
    await act(async () => {});
    expect(spy).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
