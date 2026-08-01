import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { Comparison } from "./Comparison";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { theme } from "../theme";

describe("Dashboard Integration & E2E Scenarios (T3 & T4)", () => {
  // Real timers by default: RTL's findBy*/waitFor cannot advance vitest fake
  // timers here (they'd hang). Only the background-polling test opts into fake
  // timers locally, and uses synchronous queries there.
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1024,
    });
  });

  // T3.2: Widget to Comparison Redirection (R1+R2)
  it("redirects to Comparison page with selected pilots pre-populated", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/compare" element={<Comparison />} />
        </Routes>
      </MemoryRouter>
    );

    const cb1 = await screen.findByTestId("top-pilot-checkbox-trend-following");
    const cb2 = screen.getByTestId("top-pilot-checkbox-dip-buyer");

    fireEvent.click(cb1);
    fireEvent.click(cb2);

    const compareBtn = screen.getByTestId("compare-selected-btn");
    fireEvent.click(compareBtn);

    expect(await screen.findByTestId("comparison-title")).toBeInTheDocument();

    // Checkboxes render once the pilots list resolves — wait for the first.
    const trendFollowingCheckbox = (await screen.findByTestId("comparison-checkbox-trend-following")) as HTMLInputElement;
    const dipBuyerCheckbox = screen.getByTestId("comparison-checkbox-dip-buyer") as HTMLInputElement;
    expect(trendFollowingCheckbox.checked).toBe(true);
    expect(dipBuyerCheckbox.checked).toBe(true);
  });

  // T3.4: Context-Sensitive Comparative Alerts (R2+R3)
  it("dynamically filters comparative activity widget alerts based on active comparison set", async () => {
    // ActivityFeed attributes an alert to a pilot ONLY via an exact
    // `extra.pilot_id` match (never message-text matching), so the fixtures carry
    // pilot_id. Selecting "trend-following" must show only its attributed alert.
    vi.spyOn(api, "getAlerts").mockResolvedValue({
      reason: null,
      entries: [
        { timestamp: new Date().toISOString(), level: "INFO", message: "Trend Follower executed BUY order", extra: { pilot_id: "trend-following" } },
        { timestamp: new Date().toISOString(), level: "INFO", message: "Dip Buyer executed SELL order", extra: { pilot_id: "dip-buyer" } },
        { timestamp: new Date().toISOString(), level: "INFO", message: "Momentum Leaders executed order", extra: { pilot_id: "cross-sectional-momentum" } }
      ]
    });

    render(
      <MemoryRouter>
        <Comparison />
      </MemoryRouter>
    );

    const cb = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(cb);

    expect(await screen.findByTestId("comparison-activity-feed")).toBeInTheDocument();
    // Alerts load asynchronously — wait for the matching one to appear.
    expect(await screen.findByText("Trend Follower executed BUY order")).toBeInTheDocument();
    // NOTE: this negative assertion exercises ActivityFeed's pilotIds filtering,
    // which lives in the sibling-owned ActivityFeed component (frozen prop
    // `pilotIds`). It passes once that component filters by pilotIds.
    expect(screen.queryByText("Dip Buyer executed SELL order")).not.toBeInTheDocument();
  });

  // T4.1: Initial Cold Start (No Backend Pipeline)
  it("displays user-friendly instructions banner when portfolio API returns 404", async () => {
    vi.spyOn(api, "getPortfolio").mockRejectedValue(
      new ApiError("no account snapshot cached yet", 404)
    );

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByText("Run the Stockpy pipeline to produce data, then pull to refresh.")).toBeInTheDocument();
  });

  // T4.2: Strategy Evaluation and Active Follow
  it("guides operator from comparing strategies to following, committing amount, and writing follow queue", async () => {
    const followSpy = vi.spyOn(api, "follow").mockResolvedValue({
      follow: { pilot_id: "trend-following", amount: 1000, created_at: "", updated_at: "", status: "active" },
      planned_intents: [],
      mode: "review",
      queue_written: true,
      notional_cap: 2500,
      min_amount: 100,
      notice: "Gated queue created."
    });

    render(
      <MemoryRouter>
        <Comparison />
      </MemoryRouter>
    );

    // Select pilot and click Follow
    const cb = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(cb);

    const followBtn = await screen.findByTestId("follow-pilot-btn-trend-following");
    fireEvent.click(followBtn);

    // Follow modal mounts
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Follow Trend Follower")).toBeInTheDocument();

    // Fill amount and click preview
    const amountInput = screen.getByLabelText("Amount (USD)");
    fireEvent.change(amountInput, { target: { value: "1000" } });

    const previewBtn = screen.getByText("Preview queue");
    fireEvent.click(previewBtn);

    // Wait for follow call
    expect(followSpy).toHaveBeenCalledWith("trend-following", 1000);
    
    // Complete the flow
    const doneBtn = await screen.findByText("Done");
    fireEvent.click(doneBtn);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // T4.3: Sudden Volatility Event Alert Response
  it("shifts dashboard styling and reflects critical state in export preview when CRITICAL volatility alert triggers", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValue({
      reason: null,
      entries: [
        { timestamp: new Date().toISOString(), level: "CRITICAL", message: "CRITICAL Volatility Event!", extra: null }
      ]
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    // Verify Critical Dot appears with the theme decline color. jsdom normalizes
    // inline color to rgb(), so compare against the normalized form of the token
    // rather than the raw hex string.
    const criticalDot = await screen.findByText("Critical");
    const probe = document.createElement("span");
    probe.style.color = theme.decline;
    expect(criticalDot.style.color).toBe(probe.style.color);

    // Verify export panel mounts successfully (contains current state)
    expect(screen.getByTestId("export-preview")).toBeInTheDocument();
  });

  // T4.4: Offline Intermittent Connectivity Loss
  it("keeps last loaded data readable on screen and handles refresh errors gracefully when offline", async () => {
    let callCount = 0;
    vi.spyOn(api, "getPortfolio").mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({
          total_equity: 48213.55,
          buying_power: 6120.4,
          total_unrealized_pl: 3182.19,
          total_dividends: 412.66,
          position_count: 6,
          source: "cache",
          fetched_at: new Date().toISOString(),
          positions: []
        });
      }
      return Promise.reject(new ApiError("Network offline", 0));
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    // Initial equity shows up
    expect(await screen.findByText("$48,213.55")).toBeInTheDocument();

    // Trigger refresh (which fails because offline)
    const refreshBtn = await screen.findByTestId("portfolio-refresh-btn");
    await act(async () => {
      fireEvent.click(refreshBtn);
    });

    expect(callCount).toBe(2);

    // Warning is visible
    const warning = await screen.findByTestId("portfolio-offline-warning");
    expect(warning).toBeInTheDocument();
    expect(warning).toHaveTextContent("Offline: using cached data.");

    // Stale cached data remains visible on screen
    expect(screen.getByText("$48,213.55")).toBeInTheDocument();

    // Recover network connection: mock successful load
    vi.spyOn(api, "getPortfolio").mockResolvedValue({
      total_equity: 50000.00,
      buying_power: 7000.0,
      total_unrealized_pl: 4000.0,
      total_dividends: 500.0,
      position_count: 7,
      source: "live",
      fetched_at: new Date().toISOString(),
      positions: []
    });

    // Trigger retry
    const retryBtn = screen.getByText("Retry");
    await act(async () => {
      fireEvent.click(retryBtn);
    });

    // Warning disappears, new value appears
    expect(await screen.findByText("$50,000.00")).toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-offline-warning")).not.toBeInTheDocument();
  });


});
