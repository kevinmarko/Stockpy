import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { StrategyReportCard } from "./StrategyReportCard";

describe("StrategyReportCard", () => {
  it("renders all rows with their predicted and actual data", async () => {
    render(
      <MemoryRouter>
        <StrategyReportCard />
      </MemoryRouter>
    );

    // Wait for the loading state to finish
    await waitFor(() => {
      expect(screen.getByText("Strategy Report Card")).toBeInTheDocument();
    });

    // 1. Deployable Pilot with both sides
    expect(screen.getByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText("1.12")).toBeInTheDocument();
    expect(screen.getByText("19.0%")).toBeInTheDocument();
    expect(screen.getByText("31.0%")).toBeInTheDocument();
    expect(screen.getAllByText("PASS").length).toBeGreaterThan(0);
    expect(screen.getByText("0.95")).toBeInTheDocument();
    expect(screen.getByText("$12,500.00")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();

    // 2. copula-stat-arb (predicted FAIL + real actual)
    expect(screen.getByText("Copula Stat Arb")).toBeInTheDocument();
    expect(screen.getAllByText("FAIL").length).toBeGreaterThan(0);
    expect(screen.getByText("PBO > 0.5 (Overfit)")).toBeInTheDocument();
    expect(screen.getByText("0.12")).toBeInTheDocument();
    expect(screen.getByText("$45,000.00")).toBeInTheDocument();
    expect(screen.getByText("118")).toBeInTheDocument();

    // 3. iron-condor (predicted null+reason, actual populated)
    expect(screen.getByText("Iron Condor Harvest")).toBeInTheDocument();
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
    expect(screen.getByText("Requires intraday options data")).toBeInTheDocument();
    expect(screen.getByText("1.35")).toBeInTheDocument();
    expect(screen.getByText("$8,400.00")).toBeInTheDocument();
    expect(screen.getByText("56")).toBeInTheDocument();

    // 4. zero-trade Pilot
    expect(screen.getByText("Zero Trade Strategy")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("0")).toBeInTheDocument();

    // 5. non-Pilot bucket row
    expect(screen.getByText("Legacy Discretionary")).toBeInTheDocument();
    expect(screen.getByText("Not model-driven")).toBeInTheDocument();
    expect(screen.getByText("0.88")).toBeInTheDocument();
    expect(screen.getByText("$22,000.00")).toBeInTheDocument();
    expect(screen.getByText("315")).toBeInTheDocument();

    // 6. actual side below honesty floor (e.g. n=7)
    expect(screen.getByText("New Strategy")).toBeInTheDocument();
    expect(screen.getByText("2.10")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });
});
