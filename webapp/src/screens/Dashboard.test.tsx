import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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


  // T2.1: Corrupted LocalStorage Handling
  it("falls back to default layout if localStorage is corrupted", async () => {
    localStorage.setItem("dashboard_layout", "{ invalid json }");
    renderDashboard();
    expect(await screen.findByTestId("dashboard-title")).toBeInTheDocument();
    expect(screen.getByTestId("widget-portfolio-summary")).toBeInTheDocument();
    expect(screen.getByTestId("widget-performance-curve")).toBeInTheDocument();
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

});
