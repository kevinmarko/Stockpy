import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { api } from "../api/client";
import { ApiError } from "../api/types";

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

describe("Dashboard screen (R1)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1024,
    });
  });

  // T1.1: Mount and render checking
  it("renders dashboard title and standard widgets", async () => {
    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(screen.getByTestId("widget-portfolio-summary")).toBeInTheDocument();
    expect(screen.getByTestId("widget-performance-curve")).toBeInTheDocument();
    expect(screen.getByTestId("widget-activity-feed")).toBeInTheDocument();
    expect(screen.getByTestId("widget-top-pilots")).toBeInTheDocument();
    expect(screen.getByTestId("widget-notebook-export")).toBeInTheDocument();
  });

  // T1.2: Resize widget trigger style change
  it("handles resize triggers correctly", async () => {
    renderDashboard();
    const resizeBtn = await screen.findByTestId("resize-portfolio-summary-L");
    fireEvent.click(resizeBtn);
    const widget = screen.getByTestId("widget-portfolio-summary");
    expect(widget.style.gridColumn).toBe("span 3");
  });

  // T1.3: HTML5 drag reorder simulation
  it("reorders widgets on HTML5 drag and drop events", async () => {
    renderDashboard();
    const widgetPortfolio = await screen.findByTestId("widget-portfolio-summary");
    const widgetCurve = screen.getByTestId("widget-performance-curve");

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn().mockReturnValue("0"),
    };

    fireEvent.dragStart(widgetPortfolio, { dataTransfer });
    fireEvent.drop(widgetCurve, { dataTransfer });

    const widgetsAfter = screen.getAllByTestId(/^widget-/);
    expect(widgetsAfter[1]).toHaveAttribute("data-testid", "widget-portfolio-summary");
  });

  // T1.4: Serialization of reordered layout to localStorage
  it("serializes new layout order to localStorage on reorder", async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    renderDashboard();
    const widgetPortfolio = await screen.findByTestId("widget-portfolio-summary");
    const widgetCurve = screen.getByTestId("widget-performance-curve");

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn().mockReturnValue("0"),
    };

    fireEvent.dragStart(widgetPortfolio, { dataTransfer });
    fireEvent.drop(widgetCurve, { dataTransfer });

    expect(setItemSpy).toHaveBeenCalledWith("dashboard_layout", expect.any(String));
  });

  // T1.5: Deserialization of layout on mount
  it("loads and applies valid layout settings from localStorage on mount", async () => {
    const customLayout = [
      { id: "activity-feed", title: "Activity Feed", size: "M" as const },
      { id: "portfolio-summary", title: "Portfolio Summary", size: "L" as const },
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(customLayout));

    renderDashboard();
    const widgets = await screen.findAllByTestId(/^widget-/);
    // The saved 2-widget order is applied first, then the 3 missing DEFAULT_LAYOUT
    // widgets are merged in (same behavior asserted by the merge test below).
    expect(widgets.length).toBe(5);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-activity-feed");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-portfolio-summary");
    expect(widgets[1].style.gridColumn).toBe("span 3");
  });

  // T2.1: Corrupted LocalStorage Handling
  it("falls back to default layout if localStorage is corrupted", async () => {
    localStorage.setItem("dashboard_layout", "{ invalid json }");
    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(screen.getByTestId("widget-portfolio-summary")).toBeInTheDocument();
    expect(screen.getByTestId("widget-performance-curve")).toBeInTheDocument();
  });

  // T2.2: Drags and Drops on Same Index
  it("does not update state or re-render if dropped on the same index", async () => {
    renderDashboard();
    const widgetPortfolio = await screen.findByTestId("widget-portfolio-summary");

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn().mockReturnValue("0"),
    };

    const widgetsBefore = screen.getAllByTestId(/^widget-/).map(w => w.getAttribute("data-testid"));

    // Drops onto index 0 (self)
    fireEvent.dragStart(widgetPortfolio, { dataTransfer });
    fireEvent.drop(widgetPortfolio, { dataTransfer });

    const widgetsAfter = screen.getAllByTestId(/^widget-/).map(w => w.getAttribute("data-testid"));
    expect(widgetsAfter).toEqual(widgetsBefore);
  });

  // T2.3: Ignore Invalid Drag Drop Events
  it("ignores drop events with missing or invalid index data", async () => {
    renderDashboard();
    const widgetCurve = await screen.findByTestId("widget-performance-curve");

    const dataTransfer = {
      setData: vi.fn(),
      getData: vi.fn().mockReturnValue(""),
    };

    fireEvent.dragStart(widgetCurve, { dataTransfer });
    fireEvent.drop(widgetCurve, { dataTransfer });

    const widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-portfolio-summary");
  });

  // T2.4: Cold-Start 404 handler
  it("renders widget-specific cold-start error when portfolio API fails with 404", async () => {
    vi.spyOn(api, "getPortfolio").mockRejectedValueOnce(
      new ApiError("no account snapshot cached yet", 404)
    );
    renderDashboard();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByText("Run the Stockpy pipeline to produce data, then pull to refresh.")).toBeInTheDocument();
  });

  // T2.5: Responsive Mobile Stacking
  it("sets all widgets to span 3 on mobile layout", async () => {
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 500,
    });
    
    // Trigger window resize event listener
    renderDashboard();
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });

    const widgetPortfolio = await screen.findByTestId("widget-portfolio-summary");
    expect(widgetPortfolio.style.gridColumn).toBe("span 3");
  });

  // T3.1: Mobile touch reordering buttons move widgets
  it("moves widgets up and down via reordering buttons", async () => {
    renderDashboard();
    
    // Default order should be: portfolio-summary, performance-curve, activity-feed, top-pilots, notebook-export
    let widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-portfolio-summary");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-performance-curve");

    // Move performance-curve up (index 1 to 0)
    const moveUpBtn = screen.getByTestId("move-up-performance-curve");
    fireEvent.click(moveUpBtn);

    widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-performance-curve");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-portfolio-summary");

    // Move performance-curve down (index 0 to 1)
    const moveDownBtn = screen.getByTestId("move-down-performance-curve");
    fireEvent.click(moveDownBtn);

    widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-portfolio-summary");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-performance-curve");
  });

  // T3.2: Move Up / Down button disabled states
  it("disables first widget's Move Up and last widget's Move Down buttons", async () => {
    renderDashboard();
    
    const firstMoveUp = screen.getByTestId("move-up-portfolio-summary");
    const lastMoveDown = screen.getByTestId("move-down-notebook-export");

    expect(firstMoveUp).toBeDisabled();
    expect(lastMoveDown).toBeDisabled();
  });

  // T3.3: Initializing layout with a subset of widgets in localStorage merges missing ones
  it("merges missing widgets from DEFAULT_LAYOUT when layout in localStorage has a subset", async () => {
    const subsetLayout = [
      { id: "activity-feed", title: "Activity Feed", size: "M" as const },
      { id: "portfolio-summary", title: "Portfolio Summary", size: "L" as const },
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(subsetLayout));

    renderDashboard();

    const widgets = screen.getAllByTestId(/^widget-/);
    // There are 5 DEFAULT_LAYOUT widgets. If we load 2, 3 missing should be appended.
    expect(widgets.length).toBe(5);
    
    // First two should match subsetLayout
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-activity-feed");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-portfolio-summary");
    
    // The rest should be the missing widgets (performance-curve, top-pilots, notebook-export) in any order
    const remainingIds = widgets.slice(2).map(w => w.getAttribute("data-testid"));
    expect(remainingIds).toContain("widget-performance-curve");
    expect(remainingIds).toContain("widget-top-pilots");
    expect(remainingIds).toContain("widget-notebook-export");
  });

  // T3.4: Reset layout button restores layout to DEFAULT_LAYOUT
  it("restores layout to DEFAULT_LAYOUT and updates state/rendering when Reset Layout button is clicked", async () => {
    const customLayout = [
      { id: "activity-feed", title: "Activity Feed", size: "M" as const },
      { id: "portfolio-summary", title: "Portfolio Summary", size: "L" as const },
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(customLayout));

    renderDashboard();

    let widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-activity-feed");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-portfolio-summary");

    const resetBtn = screen.getByRole("button", { name: /reset layout/i });
    fireEvent.click(resetBtn);

    widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-portfolio-summary");
    expect(widgets[1]).toHaveAttribute("data-testid", "widget-performance-curve");
    expect(widgets[2]).toHaveAttribute("data-testid", "widget-activity-feed");
    expect(widgets[3]).toHaveAttribute("data-testid", "widget-top-pilots");
    expect(widgets[4]).toHaveAttribute("data-testid", "widget-notebook-export");
  });

  // T3.5: Obsolete/deprecated widget IDs in localStorage are filtered out
  it("filters out and ignores obsolete/deprecated widget IDs on initialization", async () => {
    const mixedLayout = [
      { id: "portfolio-summary", title: "Portfolio Summary", size: "M" as const },
      { id: "obsolete-widget", title: "Obsolete Widget", size: "M" as const },
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(mixedLayout));

    renderDashboard();

    const widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets.length).toBe(5);
    const widgetIds = widgets.map(w => w.getAttribute("data-testid"));
    expect(widgetIds).not.toContain("widget-obsolete-widget");
    expect(widgets[0]).toHaveAttribute("data-testid", "widget-portfolio-summary");
  });

  // T3.6: Duplicate widget IDs in localStorage are deduplicated
  it("deduplicates duplicate widget IDs in localStorage on initialization", async () => {
    const duplicateLayout = [
      { id: "portfolio-summary", title: "Portfolio Summary 1", size: "M" as const },
      { id: "portfolio-summary", title: "Portfolio Summary 2", size: "L" as const },
      { id: "activity-feed", title: "Activity Feed", size: "M" as const },
    ];
    localStorage.setItem("dashboard_layout", JSON.stringify(duplicateLayout));

    renderDashboard();

    const widgets = screen.getAllByTestId(/^widget-/);
    expect(widgets.length).toBe(5);
    
    const portfolioSummaryWidgets = widgets.filter(w => w.getAttribute("data-testid") === "widget-portfolio-summary");
    expect(portfolioSummaryWidgets.length).toBe(1);
    expect(portfolioSummaryWidgets[0].style.gridColumn).toBe("span 2"); // size M
  });
});
