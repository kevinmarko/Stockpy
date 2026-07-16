/**
 * Activity.test.tsx — the alerts feed screen now frames the shared ActivityFeed
 * widget. It renders level-labeled alerts against the real mock API, shows the
 * "demo" chip in mock mode, and renders the honest empty-state `reason` (never a
 * fabricated alert) when the feed is empty.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Activity } from "./Activity";
import { api } from "../api/client";

function renderActivity() {
  return render(
    <MemoryRouter>
      <Activity />
    </MemoryRouter>
  );
}

describe("Activity screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the title, demo chip, and level-labeled alerts from the mock", async () => {
    renderActivity();
    expect(
      await screen.findByRole("heading", { name: "Activity" })
    ).toBeInTheDocument();
    // Mock mode surfaces the demo chip.
    expect(screen.getByText("demo")).toBeInTheDocument();
    // At least one known level label surfaces from the feed.
    expect(await screen.findByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Warning")).toBeInTheDocument();
  });

  it("an empty feed renders the honest reason, never a fabricated alert", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      entries: [],
      reason: "Alert file not configured (set ALERT_FILE_PATH to enable).",
    });
    renderActivity();
    expect(
      await screen.findByText(/Alert file not configured/)
    ).toBeInTheDocument();
  });
});
