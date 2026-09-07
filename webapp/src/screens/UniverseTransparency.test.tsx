import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import { UniverseTransparency } from "./UniverseTransparency";
import { ExplainTickerProvider } from "../context/ExplainTickerContext";
import { ExplainTickerDrawer } from "../components/ExplainTickerDrawer";
import { api } from "../api/client";

function renderUniverseTransparency() {
  return render(
    <ExplainTickerProvider>
      <UniverseTransparency />
      <ExplainTickerDrawer />
    </ExplainTickerProvider>
  );
}

describe("UniverseTransparency screen", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders header, provenance data, and 6 KPI summary cards", async () => {
    renderUniverseTransparency();

    // Title & provenance (awaiting async data resolution)
    expect(await screen.findByText(/Quotes Source:/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Universe Transparency" })).toBeInTheDocument();
    expect(screen.getByText(/Fundamentals Source:/i)).toBeInTheDocument();

    // 6 KPI summary cards
    expect(screen.getByTestId("kpi-total")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-full")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-stale")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-quotes_only")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-equity_only")).toBeInTheDocument();
    expect(screen.getByTestId("kpi-uncovered")).toBeInTheDocument();

    // Table rows rendered
    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("universe-row-NVDA")).toBeInTheDocument();
  });

  it("filters rows when filter tabs are clicked", async () => {
    renderUniverseTransparency();

    await screen.findByTestId("universe-row-AAPL");

    // Click "Issues & Gaps" tab
    const gapsTab = screen.getByTestId("filter-tab-gaps");
    await userEvent.click(gapsTab);

    // AAPL is 'full' coverage, so it should be filtered out
    expect(screen.queryByTestId("universe-row-AAPL")).not.toBeInTheDocument();

    // NVDA is 'stale' coverage, so it should still be in the gaps list
    expect(screen.getByTestId("universe-row-NVDA")).toBeInTheDocument();

    // Click "Held" tab
    const heldTab = screen.getByTestId("filter-tab-held");
    await userEvent.click(heldTab);

    // Both AAPL and NVDA are held
    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("universe-row-NVDA")).toBeInTheDocument();

    // Click "All" tab to reset
    const allTab = screen.getByTestId("filter-tab-all");
    await userEvent.click(allTab);
    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
  });

  it("filters rows when typing in the search input", async () => {
    renderUniverseTransparency();

    await screen.findByTestId("universe-row-AAPL");

    const searchInput = screen.getByTestId("search-input");
    await userEvent.type(searchInput, "AAPL");

    expect(screen.getByTestId("universe-row-AAPL")).toBeInTheDocument();
    expect(screen.queryByTestId("universe-row-NVDA")).not.toBeInTheDocument();

    // Clear search and type a non-existent ticker
    await userEvent.clear(searchInput);
    await userEvent.type(searchInput, "NONEXISTENT");

    expect(screen.getByTestId("empty-filtered-state")).toBeInTheDocument();
    expect(screen.queryByTestId("universe-row-AAPL")).not.toBeInTheDocument();
  });

  it("clicking (ⓘ) info button on a symbol row opens the ExplainTickerDrawer", async () => {
    renderUniverseTransparency();

    await screen.findByTestId("universe-row-AAPL");

    const explainBtn = screen.getByTestId("explain-ticker-btn-AAPL");
    expect(explainBtn).toBeInTheDocument();

    await userEvent.click(explainBtn);

    // Verify ExplainTickerDrawer is mounted and opened for AAPL
    expect(await screen.findByTestId("explain-ticker-drawer")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "AAPL" })).toBeInTheDocument();
  });

  it("triggers universe sync when Sync Universe button is clicked", async () => {
    const postSyncSpy = vi.spyOn(api, "postDataSync").mockResolvedValueOnce({
      report: {
        generated_at: new Date().toISOString(),
        positions: ["AAPL"],
        watchlists: {},
        symbols: {},
        provider_source: "alpaca",
        fundamentals_source: "yahoo",
      },
      default_tickers: ["AAPL", "MSFT", "NVDA"],
      applies: "next_daemon_restart",
      note: "Discovered 3 symbols",
    });

    renderUniverseTransparency();

    await screen.findByRole("heading", { name: "Universe Transparency" });

    const syncBtn = screen.getByTestId("sync-now-button");
    await userEvent.click(syncBtn);

    expect(postSyncSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Sync completed: 3 tickers configured/i)).toBeInTheDocument();
  });

  it("handles symbol re-inclusion mutation", async () => {
    // Override getSyncReport with an excluded symbol
    vi.spyOn(api, "getSyncReport").mockResolvedValueOnce({
      generated_at: new Date().toISOString(),
      positions: [],
      watchlists: {},
      symbols: {
        EXCL: {
          symbol: "EXCL",
          coverage: "uncovered",
          held: false,
          quantity: 0,
          avg_cost: null,
          current_price: null,
          cost_basis_delta_per_share: null,
          market_value: null,
          is_stale_quote: false,
          quote_source: "none",
          has_fundamentals: false,
          forecast_available: false,
          watchlists: [],
          diagnostic: "drop_threshold_reached",
          rating_consecutive_bad_cycles: 5,
          rating_excluded: true,
        },
      },
      provider_source: "alpaca",
      fundamentals_source: "yahoo",
    });

    const reincludeSpy = vi.spyOn(api, "reincludeSymbol").mockResolvedValueOnce({
      symbol: "EXCL",
      reincluded: true,
    });

    renderUniverseTransparency();

    await screen.findByTestId("universe-row-EXCL");
    expect(screen.getByText("Excluded")).toBeInTheDocument();
    expect(screen.getByText("5 bad cycle(s)")).toBeInTheDocument();

    const reincludeBtn = screen.getByTestId("reinclude-btn-EXCL");
    await userEvent.click(reincludeBtn);

    expect(reincludeSpy).toHaveBeenCalledWith("EXCL");
  });
});
