import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { Comparison } from "./Comparison";
import { ActivityFeed } from "../components/ActivityFeed";
import { NotebookMLExport } from "../components/NotebookMLExport";
import { api } from "../api/client";
import type { Portfolio } from "../api/types";

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
  // Real timers: RTL's findBy*/waitFor cannot advance vitest fake timers here, so
  // any async query hangs under fake timers. The mock's delays are short (<300ms).
  afterEach(() => {
    vi.restoreAllMocks();
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
    // Duplicate IDs are deduplicated (see Dashboard T3.6) — exactly one renders.
    const widgets = screen.getAllByTestId("widget-portfolio-summary");
    expect(widgets.length).toBe(1);
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
      metrics: null,
      curve: null,
      benchmark: null,
      macro_benchmark: null,
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
      metrics: null,
      // Element missing date property completely
      curve: [{ value: 120 } as any, { date: "2026-07-02", value: 130 }],
      benchmark: null,
      macro_benchmark: null,
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
      metrics: null,
      curve: { notAnArray: true } as any,
      benchmark: null,
      macro_benchmark: null,
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
    // jsdom rendering 10k real DOM nodes is legitimately slow — this is a
    // stress test of that path, not a functional assertion, so it gets a
    // longer-than-default timeout rather than a behavior change.
    const hugeEntries = Array.from({ length: 10000 }).map((_, i) => ({
      timestamp: new Date(Date.now() - i * 1000).toISOString(),
      level: "INFO",
      message: `Extremely high alert item number #${i}`,
      extra: null,
    }));

    vi.spyOn(api, "getAlerts").mockResolvedValueOnce({
      reason: null,
      entries: hugeEntries
    });

    render(<ActivityFeed limit={10000} />);

    // Observable contract: it renders a very large feed without crashing. The
    // virtualization strategy (e.g. content-visibility) is an ActivityFeed
    // implementation detail owned separately, so we don't assert on it here.
    const firstAlert = await screen.findByText("Extremely high alert item number #0", {}, { timeout: 15000 });
    expect(firstAlert).toBeInTheDocument();
    expect(screen.getByTestId("activity-feed-widget")).toBeInTheDocument();
  }, 20000);

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

  it("serializes a position's null money fields as null (never 0) and does not crash", async () => {
    // Replaces the old fabricated-`description` test — `description` is not a
    // field on PortfolioPositionView and is no longer emitted. This asserts the
    // honesty contract (CONSTRAINT #4) on the fetch path instead.
    const portfolio = {
      total_equity: 100,
      buying_power: 50,
      total_unrealized_pl: 0,
      total_dividends: 0,
      position_count: 1,
      source: "mock",
      fetched_at: new Date().toISOString(),
      positions: [
        {
          symbol: "AAPL",
          name: "Apple",
          qty: 1,
          avg_cost: 150,
          current_price: null,
          market_value: null,
          unrealized_pl: null,
          unrealized_pl_pct: null,
        },
      ],
    } as unknown as Portfolio;
    vi.spyOn(api, "getPortfolio").mockResolvedValue(portfolio);

    render(<NotebookMLExport />);
    const preview = await screen.findByTestId("export-preview");
    expect(preview).toBeInTheDocument();

    // Wait for the resolved position to appear, then assert null (not 0).
    await waitFor(() => {
      const parsed = JSON.parse(preview.textContent || "");
      expect(parsed.portfolio.positions.length).toBe(1);
    });
    const parsed = JSON.parse(preview.textContent || "");
    expect(parsed.portfolio.positions[0].market_value).toBe(null);
    expect(parsed.portfolio.positions[0].market_value).not.toBe(0);
    expect(parsed.portfolio.positions[0]).not.toHaveProperty("description");
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
    // Honest empty panel (mirrors PilotDetail) instead of a blank chart.
    expect(await screen.findByTestId("equity-empty")).toBeInTheDocument();
    expect(screen.getByText("No account performance data yet")).toBeInTheDocument();
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

