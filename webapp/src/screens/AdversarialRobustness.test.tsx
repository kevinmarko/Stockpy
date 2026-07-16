import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { Comparison } from "./Comparison";
import { ActivityFeed } from "../components/ActivityFeed";
import { NotebookMLExport } from "../components/NotebookMLExport";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { theme } from "../theme";

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

function renderComparison() {
  return render(
    <MemoryRouter>
      <Comparison />
    </MemoryRouter>
  );
}

describe("Adversarial Robustness and Edge Case Suite", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
  });

  // --- 1. Dashboard Widget Layout Failures ---

  it("handles completely null layout array in localStorage by falling back to DEFAULT_LAYOUT", async () => {
    localStorage.setItem("dashboard_layout", JSON.stringify([null]));
    
    // If it throws an uncaught error, this test fails.
    renderDashboard();
    
    // We expect it to recover or fall back so the dashboard title still renders.
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
  });

  it("handles layout array with invalid object structures (missing size and title) gracefully", async () => {
    const corruptLayout = [{ id: "portfolio-summary" }];
    localStorage.setItem("dashboard_layout", JSON.stringify(corruptLayout));

    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    
    // Check if portfolio-summary widget is rendered even with missing fields
    expect(screen.getByTestId("widget-portfolio-summary")).toBeInTheDocument();
  });

  it("handles layout with duplicate widget IDs without crashing", async () => {
    const dupLayout = [
      { id: "portfolio-summary", title: "Portfolio 1", size: "M" as const },
      { id: "portfolio-summary", title: "Portfolio 2", size: "M" as const }
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(dupLayout));

    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    const widgets = screen.getAllByTestId("widget-portfolio-summary");
    expect(widgets.length).toBe(2);
  });

  it("handles widgets with unsupported/invalid sizes by falling back to default size style", async () => {
    const invalidSizeLayout = [
      { id: "portfolio-summary", title: "Portfolio Summary", size: "XXL" as any }
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(invalidSizeLayout));

    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    const widget = screen.getByTestId("widget-portfolio-summary");
    expect(widget).toBeInTheDocument();
    // Default size fallback style is gridColumn: span 2
    expect(widget.style.gridColumn).toBe("span 2");
  });

  // --- 2. Comparison Screen and Empty Database Mock Curves ---

  it("handles comparison where a strategy performance curve API returns null curve", async () => {
    vi.spyOn(api, "getPerformance").mockResolvedValueOnce({
      range: "3M",
      curve: null as any
    });

    renderComparison();
    const checkbox = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(checkbox);

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    // Since the curve is null, the overlaid performance should display the empty state.
    expect(await screen.findByText("No performance curve data available for selected pilots.")).toBeInTheDocument();
  });

  it("does not crash when performance curve data elements are missing date or value fields", async () => {
    vi.spyOn(api, "getPerformance").mockResolvedValueOnce({
      range: "3M",
      // Element missing date property completely
      curve: [{ value: 120 } as any, { date: "2026-07-02", value: 130 }]
    });

    renderComparison();
    const checkbox = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(checkbox);

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    // The component should render the comparison screen and not crash due to the missing date
    expect(screen.getByTestId("comparison-screen")).toBeInTheDocument();
  });

  it("handles a non-array response for performance curve gracefully", async () => {
    vi.spyOn(api, "getPerformance").mockResolvedValueOnce({
      range: "3M",
      curve: { notAnArray: true } as any
    });

    renderComparison();
    const checkbox = await screen.findByTestId("comparison-checkbox-trend-following");
    fireEvent.click(checkbox);

    expect(await screen.findByText("Key Metrics Comparison")).toBeInTheDocument();
    // Should not crash, and should display the empty curve message.
    expect(screen.getByText("No performance curve data available for selected pilots.")).toBeInTheDocument();
  });

  // --- 3. Activity Feed and API Failures ---

  it("crashes or recovers when API returns null or missing entries in alerts feed", async () => {
    // We expect this to fail or crash if ActivityFeed lacks defense against null/missing entries.
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: "API error details",
      entries: null as any
    });

    render(<ActivityFeed limit={5} />);
    
    // We want to verify if it throws or shows an empty state/error.
    // If it crashes, the test suite itself catches the crash.
    const container = await screen.findByTestId("activity-feed-widget");
    expect(container).toBeInTheDocument();
  });

  it("handles extremely high numbers of alerts (10,000 items) without crashing or layout corruption", async () => {
    const hugeEntries = Array.from({ length: 10000 }).map((_, i) => ({
      timestamp: new Date(Date.now() - i * 1000).toISOString(),
      level: "INFO",
      message: `Extremely high alert item number #${i}`
    }));

    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: hugeEntries
    });

    render(<ActivityFeed limit={10000} />);
    
    // Verify it renders the container. It should also have contentVisibility set to auto for virtualization.
    const firstAlert = await screen.findByText("Extremely high alert item number #0");
    expect(firstAlert).toBeInTheDocument();
    
    const container = firstAlert.closest("div")?.parentElement;
    expect(container).toBeInTheDocument();
    expect(container?.style.contentVisibility).toBe("auto");
  });

  // --- 4. NotebookML Export Screen robust handling ---

  it("does not crash when portfolio API returns a null or missing positions array", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValueOnce({
      total_equity: 10000,
      buying_power: 2000,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 0,
      source: "mock",
      fetched_at: new Date().toISOString(),
      positions: null as any
    });

    render(<NotebookMLExport />);
    const preview = await screen.findByTestId("export-preview");
    expect(preview).toBeInTheDocument();

    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.positions).toEqual([]);
  });

  it("handles non-string values in position description during replace operation", async () => {
    vi.spyOn(api, "getPortfolio").mockResolvedValueOnce({
      total_equity: 100,
      buying_power: 50,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 1,
      source: "mock",
      fetched_at: new Date().toISOString(),
      positions: [{
        symbol: "AAPL",
        qty: 1,
        avg_cost: 150,
        current_price: 155,
        market_value: 155,
        description: 12345 as any // non-string value!
      } as any]
    });

    render(<NotebookMLExport />);
    const preview = await screen.findByTestId("export-preview");
    expect(preview).toBeInTheDocument();
    
    // If it doesn't crash, the test passes.
  });

  it("handles 'comparison_selected_ids' in localStorage set to 'null' without throwing", async () => {
    localStorage.setItem("comparison_selected_ids", "null");
    renderComparison();
    expect(await screen.findByTestId("comparison-title")).toBeInTheDocument();
  });

  it("handles a non-array curve response for getEquityCurve in Dashboard without crashing", async () => {
    vi.spyOn(api, "getEquityCurve").mockResolvedValueOnce({
      range: "3M",
      curve: { notAnArray: true } as any
    });

    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(await screen.findByText("No curve data available.")).toBeInTheDocument();
  });

  it("handles getAlerts returning entries containing null safely in ActivityFeed", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: [null as any]
    });

    render(<ActivityFeed limit={5} />);
    const container = await screen.findByTestId("activity-feed-widget");
    expect(container).toBeInTheDocument();
    expect(await screen.findByTestId("empty-alerts")).toBeInTheDocument();
    expect(screen.getByText("No alerts yet.")).toBeInTheDocument();
  });
});

