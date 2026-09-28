import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { StrategyReportCard } from "./StrategyReportCard";

function renderScreen() {
  return render(
    <MemoryRouter>
      <StrategyReportCard />
    </MemoryRouter>
  );
}

describe("StrategyReportCard", () => {
  it("renders all rows with their predicted and actual data", async () => {
    renderScreen();

    // 1. Deployable Pilot with both sides populated: "trend-following"
    const tf = await screen.findByTestId("report-card-row-trend-following");
    expect(within(tf).getByText("Trend Follower")).toBeInTheDocument();
    expect(within(tf).getByText("1.12")).toBeInTheDocument(); // predicted sharpe
    expect(within(tf).getByText("19.0%")).toBeInTheDocument(); // predicted max DD
    expect(within(tf).getByText("31.0%")).toBeInTheDocument(); // predicted PBO
    expect(within(tf).getByText("PASS")).toBeInTheDocument();
    expect(within(tf).getByText("0.95")).toBeInTheDocument(); // actual realized sharpe proxy
    expect(within(tf).getByText("$12,500.00")).toBeInTheDocument(); // actual max DD usd
    expect(within(tf).getByText("42")).toBeInTheDocument(); // trade count

    // 2. iron-condor: a retired options Pilot (removed from the catalog with
    // the options desk, 2026-09 step 4a). Its historical paper trades still
    // attribute, as a non-Pilot row whose predicted side is fully nulled with
    // the backend's retired reason -- never a fabricated backtest.
    const ic = screen.getByTestId("report-card-row-iron-condor");
    expect(within(ic).getByText("Iron Condor")).toBeInTheDocument();
    expect(within(ic).getByText("Retired")).toBeInTheDocument();
    expect(within(ic).getByText("non-pilot bucket")).toBeInTheDocument();
    expect(
      within(ic).getByText("retired options pilot (options desk removed 2026-09)")
    ).toBeInTheDocument();
    // Every nulled predicted field (sharpe/max DD/PBO/DSR/Gate) renders an
    // honest "—", never a fabricated "N/A" or "0".
    expect(within(ic).getAllByText("—").length).toBeGreaterThanOrEqual(5);
    expect(within(ic).queryByText("N/A")).not.toBeInTheDocument();
    expect(within(ic).queryByText("0")).not.toBeInTheDocument();
    // Actual side is still populated (this retired pilot HAS paper trades).
    expect(within(ic).getByText("1.35")).toBeInTheDocument();
    expect(within(ic).getByText("$8,400.00")).toBeInTheDocument();
    expect(within(ic).getByText("56")).toBeInTheDocument();
    expect(screen.queryByTestId("report-card-row-copula-stat-arb")).not.toBeInTheDocument();

    // 4. zero-trade Pilot: real predicted metrics, but zero paper trades ->
    // every actual evaluative field nulls out with an honest reason, while
    // trade_count (a real, meaningful 0) still renders.
    const zt = screen.getByTestId("report-card-row-zero-trade");
    expect(within(zt).getByText("Zero Trade Strategy")).toBeInTheDocument();
    expect(within(zt).getByText("PASS")).toBeInTheDocument();
    expect(within(zt).getByText("0")).toBeInTheDocument(); // trade_count, a real 0
    expect(within(zt).getByText("insufficient sample (n=0)")).toBeInTheDocument();

    // 5. non-Pilot bucket row: predicted side is fully null with the
    // backend's fixed "non-pilot bucket" reason string; a chip marks it as
    // not a catalog Pilot.
    const npb = screen.getByTestId("report-card-row-non-pilot-bucket");
    expect(within(npb).getByText("Legacy Discretionary")).toBeInTheDocument();
    expect(within(npb).getAllByText("non-pilot bucket").length).toBeGreaterThanOrEqual(1);
    expect(within(npb).getByText("0.88")).toBeInTheDocument();
    expect(within(npb).getByText("$22,000.00")).toBeInTheDocument();
    expect(within(npb).getByText("315")).toBeInTheDocument();

    // 6. actual side below the honesty floor (n=7)
    const ns = screen.getByTestId("report-card-row-new-strategy");
    expect(within(ns).getByText("New Strategy")).toBeInTheDocument();
    expect(within(ns).getByText("2.10")).toBeInTheDocument();
    expect(within(ns).getByText("7")).toBeInTheDocument();
    expect(within(ns).getByText("insufficient sample (n=7)")).toBeInTheDocument();
  });

  it("marks non-catalog rows with a 'non-pilot bucket' chip, never silently", async () => {
    renderScreen();

    // The row renders "non-pilot bucket" TWICE: once as the header chip
    // marking it as not a catalog Pilot, and once as the backend's real
    // `predicted.reason` string -- both honest, neither fabricated.
    const npb = await screen.findByTestId("report-card-row-non-pilot-bucket");
    expect(within(npb).getAllByText("non-pilot bucket").length).toBeGreaterThanOrEqual(2);

    // A real catalog Pilot row must NOT carry that chip.
    const tf = screen.getByTestId("report-card-row-trend-following");
    expect(within(tf).queryByText(/non-pilot bucket/)).not.toBeInTheDocument();
  });

  it("never renders the string 'N/A' anywhere on the page (CONSTRAINT #4)", async () => {
    renderScreen();
    await screen.findByTestId("report-card-row-trend-following");
    expect(screen.queryByText("N/A")).not.toBeInTheDocument();
  });
});
